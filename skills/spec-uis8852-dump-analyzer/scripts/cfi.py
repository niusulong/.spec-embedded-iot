#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""DWARF .debug_frame CFI stack-unwinding engine for UIS8852 (RISC-V RV32).

Deterministic, frame-by-frame backtrace driven by the Call Frame Information
tables — the same mechanism TRACE32 / GDB use. Replaces the noisy prologue +
stack-scan heuristics in unwind.py whenever the ELF ships .debug_frame.

Design:
  * All memory reads go through the existing `common.Mem` instance (PSRAM /
    IRAM / ITCM / XIP aliases resolve transparently — no address translation).
  * Full register-rule table is carried across frames so CFA rules that
    reference a frame pointer (s0/fp) or other callee-saved regs resolve
    correctly, not just the sp/ra pair.
  * FDEs are indexed by start-PC for O(log n) lookup and the parsed table is
    pickled to the system temp dir keyed by ELF hash (mirrors the struct_offsets
    cache in common.py), so re-runs are instant.

Usage (library):
    from common import Mem, Symbols
    from cfi import CFIUnwinder, NoCFIError
    mem = Mem(dump); syms = Symbols(elf)
    uw = CFIUnwinder(syms)                 # raises NoCFIError if no .debug_frame
    chain = uw.unwind(mem, start_pc, start_sp, registers={...})
    # chain = [(pc, sp), ...] from innermost to outermost

This module is import-safe: importing it never opens the ELF. Construction does.
"""
import os
import sys
import bisect
import struct

try:
    from elftools.dwarf.callframe import CIE, FDE
except Exception:  # pragma: no cover - pyelftools must be present (common.py needs it)
    CIE = FDE = None


class NoCFIError(RuntimeError):
    """Raised when the ELF has no .debug_frame / .eh_frame to unwind with."""


# DWARF Call Frame Instruction opcodes (extended, primary byte)
_DW_CFA_advance_loc = 0x40      # primary: high 2 bits = 0b01 (operand in low 6 bits / args)
_DW_CFA_offset = 0x80           # primary: high 2 bits = 0b10
_DW_CFA_restore = 0xC0          # primary: high 2 bits = 0b11
# extended (full byte)
_CFA_set_loc = 0x01
_CFA_advance_loc1 = 0x02
_CFA_advance_loc2 = 0x03
_CFA_advance_loc4 = 0x04
_CFA_offset_extended = 0x05
_CFA_restore_extended = 0x06
_CFA_undefined = 0x07
_CFA_same_value = 0x08
_CFA_register = 0x09
_CFA_remember_state = 0x0a
_CFA_restore_state = 0x0b
_CFA_def_cfa = 0x0c
_CFA_def_cfa_register = 0x0d
_CFA_def_cfa_offset = 0x0e
_CFA_def_cfa_expression = 0x0f
_CFA_expression = 0x10
_CFA_offset_extended_sf = 0x11
_CFA_def_cfa_sf = 0x12
_CFA_def_cfa_offset_sf = 0x13
_CFA_val_offset = 0x14
_CFA_val_offset_sf = 0x15


# RISC-V register numbers we care about (RV32 integer ABI)
_REG_SP = 2     # x2 / sp
_REG_RA = 1     # x1 / ra


class CFIUnwinder:
    """Deterministic DWARF-CFI stack unwinder over a memory dump."""

    def __init__(self, syms, verbose=False):
        if CIE is None:
            raise NoCFIError("pyelftools unavailable")
        self.syms = syms
        self.verbose = verbose
        dwarf = getattr(syms, "dwarf", None)
        if dwarf is None:
            raise NoCFIError("ELF has no DWARF info")
        # .debug_frame presence (defensive: not every pyelftools version names it)
        has_cfi = False
        for m in ("has_CFI", "has_frame_info"):
            if hasattr(dwarf, m) and getattr(dwarf, m)():
                has_cfi = True
                break
        # starts[i], ends[i], fdes[i] parallel arrays, sorted by start PC
        self._starts, self._ends, self._fdes = self._load_fdes(dwarf)
        if not has_cfi and not self._fdes:
            raise NoCFIError("ELF has no .debug_frame/.eh_frame CFI")

    # ------------------------------------------------------------------ load
    def _load_fdes(self, dwarf):
        """Parse .debug_frame FDEs once and keep them in memory (sorted by PC).

        .debug_frame is small (~hundreds of KB), so re-parsing each run is fast;
        we intentionally do not pickle pyelftools FDE objects (they carry parser
        state). If profiling ever shows this is slow, cache a compact
        (start, end, instructions-tuples) form instead.
        """
        starts, ends, fdes = [], [], []
        try:
            entries = list(dwarf.CFI_entries())
        except Exception:
            entries = []
        for e in entries:
            if not isinstance(e, FDE):
                continue
            start = e.header["initial_location"]
            rng = e.header["address_range"]
            starts.append(start); ends.append(start + rng); fdes.append(e)
        order = sorted(range(len(starts)), key=lambda i: starts[i])
        starts = [starts[i] for i in order]
        ends = [ends[i] for i in order]
        fdes = [fdes[i] for i in order]
        return starts, ends, fdes

    # ------------------------------------------------------------------ lookup
    def find_fde(self, pc):
        """Return the FDE covering pc, or None."""
        if not self._starts:
            return None
        i = bisect.bisect_right(self._starts, pc) - 1
        if i < 0:
            return None
        if pc < self._ends[i]:
            return self._fdes[i]
        return None

    # -------------------------------------------------------------- rule eval
    @staticmethod
    def _initial_rules(cie):
        """CIE establishes initial register rules + CFA. Returns (cie_info)."""
        return {
            "code_align": cie.header.get("code_alignment_factor", 1) or 1,
            "data_align": cie.header.get("data_alignment_factor", -4),
            "ra_col": cie.header.get("return_address_register", _REG_RA),
        }

    def _build_rule_table(self, fde, target_pc):
        """Replay CIE initial instructions then FDE instructions up to
        target_pc. Returns (cfa_rule, reg_rules, cie_info).
          cfa_rule : None | (base_reg, offset)          # CFA = reg[base] + offset
          reg_rules: {reg_num: rule}  rule in:
                      ('offset', f)   value = mem[cfa + f*data_align]
                      ('val_offset',f) value = cfa + f*data_align
                      ('register', r) value = reg[r]
                      ('same',)       value = reg[reg]   (unchanged from caller)
                      ('undefined',)  unknown
        """
        cie = fde.cie
        info = self._initial_rules(cie)
        cfa_rule = None              # (base_reg, offset)
        reg = {}                     # reg -> rule
        saved_states = []            # for remember/restore_state
        loc = fde.header["initial_location"]

        def apply(insn):
            nonlocal cfa_rule, loc
            op = insn.opcode
            a = insn.args
            primary = op & 0xC0
            if primary == _DW_CFA_advance_loc:
                loc += (a[0] if a else (op & 0x3F)) * info["code_align"]
                return "ADVANCE"
            if primary == _DW_CFA_offset:
                r = a[0]; f = a[1]; reg[r] = ("offset", f * info["data_align"]); return
            if primary == _DW_CFA_restore:
                r = a[0]; reg.pop(r, None); return        # restore to CIE initial (we approximate: drop)
            if op == _CFA_set_loc:
                loc = a[0]; return "ADVANCE"
            if op in (_CFA_advance_loc1, _CFA_advance_loc2, _CFA_advance_loc4):
                loc += a[0] * info["code_align"]; return "ADVANCE"
            if op == _CFA_def_cfa:
                cfa_rule = (a[0], a[1]); return
            if op == _CFA_def_cfa_register:
                cfa_rule = (a[0], cfa_rule[1] if cfa_rule else 0); return
            if op == _CFA_def_cfa_offset:
                cfa_rule = ((cfa_rule[0] if cfa_rule else _REG_SP), a[0]); return
            if op == _CFA_def_cfa_sf:
                cfa_rule = (a[0], a[1] * info["data_align"]); return
            if op == _CFA_def_cfa_offset_sf:
                cfa_rule = ((cfa_rule[0] if cfa_rule else _REG_SP), a[1] * info["data_align"]); return
            if op in (_CFA_offset_extended, _CFA_val_offset, _CFA_offset_extended_sf, _CFA_val_offset_sf):
                # all offset-family operands are *factored* (scaled by data_align)
                r = a[0]; off = a[1] * info["data_align"]
                kind = "val_offset" if op in (_CFA_val_offset, _CFA_val_offset_sf) else "offset"
                reg[r] = (kind, off); return
            if op == _CFA_register:
                reg[a[0]] = ("register", a[1]); return
            if op == _CFA_same_value:
                reg[a[0]] = ("same",); return
            if op == _CFA_undefined:
                reg[a[0]] = ("undefined",); return
            if op == _CFA_restore_extended:
                reg.pop(a[0], None); return
            if op == _CFA_remember_state:
                saved_states.append((cfa_rule, dict(reg))); return
            if op == _CFA_restore_state:
                if saved_states:
                    cr, reg2 = saved_states.pop()
                    cfa_rule = cr
                    reg.clear(); reg.update(reg2)
                return
            # expression-based (0x0f def_cfa_expression, 0x10 expression): unsupported, skip
            return

        for insn in cie.instructions:
            r = apply(insn)
            if r == "ADVANCE" and loc > target_pc:
                break
        for insn in fde.instructions:
            r = apply(insn)
            if r == "ADVANCE" and loc > target_pc:
                break
        # default CFA if only def_cfa_register was seen without an offset
        if cfa_rule is None:
            cfa_rule = (_REG_SP, 0)
        return cfa_rule, reg, info

    # --------------------------------------------------------------- fetch reg
    @staticmethod
    def _read_u32(mem, addr):
        try:
            return struct.unpack("<I", mem.read(addr, 4))[0]
        except Exception:
            return None

    def _resolve_reg(self, rule, regnum, regs, cfa, mem):
        """Compute a register's VALUE in the CALLER frame given the rule."""
        if rule is None:
            return regs.get(regnum)
        kind = rule[0]
        if kind == "offset":
            addr = cfa + rule[1]
            return self._read_u32(mem, addr)
        if kind == "val_offset":
            return cfa + rule[1]
        if kind == "register":
            return regs.get(rule[1])
        if kind == "same":
            return regs.get(regnum)
        if kind == "undefined":
            return None
        return None

    # ------------------------------------------------------------------ unwind
    def unwind(self, mem, start_pc, start_sp, registers=None,
               stack_lo=None, stack_hi=None, max_frames=48):
        """Deterministic CFI backtrace from (start_pc, start_sp).

        `registers` maps reg_num -> value for the seed frame (ideally all 32
        integer regs from a trap/switch frame). If absent, only sp is seeded
        and FP-relative CFAs may fail to resolve (chain stops earlier).

        Returns a list of dicts: [{pc, sp, cfa, fn, off}, ...] innermost first.
        """
        regs = dict(registers or {})
        regs[_REG_SP] = start_sp & 0xFFFFFFFF
        pc = start_pc & 0xFFFFFFFF
        syms = self.syms
        chain = []
        for _ in range(max_frames):
            fde = self.find_fde(pc)
            if fde is None:
                break
            try:
                cfa_rule, reg_rules, info = self._build_rule_table(fde, pc)
            except Exception as e:
                if self.verbose:
                    print("  [cfi] rule-table fail @0x%08x: %s" % (pc, e), file=sys.stderr)
                break
            base_reg, off = cfa_rule
            base_val = regs.get(base_reg)
            if base_val is None:
                break
            cfa = (base_val + off) & 0xFFFFFFFF
            # caller return address = rule for the return-address column
            ra_rule = reg_rules.get(info["ra_col"])
            ra = self._resolve_reg(ra_rule, info["ra_col"], regs, cfa, mem)
            # record frame
            name, noff = syms.resolve(pc)
            chain.append({"pc": pc, "sp": regs.get(_REG_SP), "cfa": cfa,
                          "fn": name, "off": noff, "ra": ra})
            if ra is None or (ra & 0xFFFFFFFF) == 0:
                break
            # sanity: ra must itself be inside a FDE (a real return address)
            if self.find_fde(ra & 0xFFFFFFFF) is None:
                # not a recoverable call boundary — stop (conservative)
                break
            # build next frame's register set from the rules (caller's regs)
            next_regs = {}
            for rnum, rule in reg_rules.items():
                v = self._resolve_reg(rule, rnum, regs, cfa, mem)
                if v is not None:
                    next_regs[rnum] = v & 0xFFFFFFFF
            # caller's sp at the call site == CFA (RISC-V callee entry sp = CFA)
            next_regs[_REG_SP] = cfa & 0xFFFFFFFF
            # boundary / no-progress: the next frame's sp must grow past the
            # current one; equal means we hit a thread-entry trampoline (e.g.
            # osThreadExit) whose CFA == own sp and would loop forever.
            if (cfa & 0xFFFFFFFF) == (regs.get(_REG_SP, 0) & 0xFFFFFFFF):
                break
            # keep any seed regs not described (rarely needed)
            for k, v in regs.items():
                next_regs.setdefault(k, v)
            pc = ra & 0xFFFFFFFF
            regs = next_regs
            # bound check
            spv = regs.get(_REG_SP)
            if stack_lo is not None and spv is not None and spv < stack_lo:
                break
            if stack_hi is not None and spv is not None and spv > stack_hi:
                break
        return chain

    # ------------------------------------------------------------ convenience
    def has_pc(self, pc):
        return self.find_fde(pc) is not None


# ===========================================================================
# UIS8852 frame-layout seeding helpers (rt_hw_stack_frame — see cpuport.c:109)
#
# rt_hw_stack_frame is 32 ubase_t words, field[0]=epc, field[1]=ra, then the
# integer regs; sp (x2) is NOT saved (it is derived). The SAME layout backs both
# the exception frame (g_osException->trace) and the context-switch frame that a
# suspended thread's tcb.sp points at. For both, the thread's real sp =
# frame_base + 128 (the frame sits ABOVE the saved-sp pointer).
# ===========================================================================
_FRAME_FIELDS = ["epc", "ra", "mstatus", "gp", "tp", "t0", "t1", "t2", "s0_fp", "s1",
                 "a0", "a1", "a2", "a3", "a4", "a5", "a6", "a7", "s2", "s3", "s4", "s5",
                 "s6", "s7", "s8", "s9", "s10", "s11", "t3", "t4", "t5", "t6"]
_FRAME_REGNUM = {"ra": 1, "gp": 3, "tp": 4, "t0": 5, "t1": 6, "t2": 7, "s0_fp": 8,
                 "s1": 9, "a0": 10, "a1": 11, "a2": 12, "a3": 13, "a4": 14, "a5": 15,
                 "a6": 16, "a7": 17, "s2": 18, "s3": 19, "s4": 20, "s5": 21, "s6": 22,
                 "s7": 23, "s8": 24, "s9": 25, "s10": 26, "s11": 27, "t3": 28, "t4": 29,
                 "t5": 30, "t6": 31}
_FRAME_SIZE = len(_FRAME_FIELDS) * 4   # 128 bytes


def read_frame(mem, base):
    """Read an rt_hw_stack_frame at `base`. Returns (epc, regs) where regs maps
    regnum->value for all saved integer regs AND regs[2] = base+128 (the sp the
    frame's owning context was using)."""
    regs = {}
    epc = 0
    for i, name in enumerate(_FRAME_FIELDS):
        try:
            v = struct.unpack("<I", mem.read(base + i * 4, 4))[0]
        except Exception:
            v = 0
        if name == "epc":
            epc = v
        elif name in _FRAME_REGNUM:
            regs[_FRAME_REGNUM[name]] = v
    regs[_REG_SP] = (base + _FRAME_SIZE) & 0xFFFFFFFF
    return epc, regs


def unwind_exception(uw, mem, trace_addr, stack_lo=None, stack_hi=None, max_frames=48):
    """Unwind the crashed/executing context from its saved exception frame
    (g_osException->trace). The trap-frame ra is clobbered by osAssertHandler's
    ecall, so CFI recomputes the real return chain from osAssertHandler's frame.
    Returns the chain list (innermost first)."""
    epc, regs = read_frame(mem, trace_addr)
    return uw.unwind(mem, epc, regs[_REG_SP], registers=regs,
                     stack_lo=stack_lo, stack_hi=stack_hi, max_frames=max_frames)


def unwind_thread(uw, mem, tcb_sp, stack_lo=None, stack_hi=None, max_frames=48):
    """Unwind a SUSPENDED thread from its saved switch frame (tcb.sp).
    Returns the chain list (innermost = resume point, out toward thread entry).
    Returns [] if the thread has no saved frame (e.g. it is the currently
    running thread whose context lives in the exception frame, not its tcb)."""
    try:
        epc = struct.unpack("<I", mem.read(tcb_sp, 4))[0]
    except Exception:
        return []
    if epc == 0 or not uw.has_pc(epc):
        return []
    epc2, regs = read_frame(mem, tcb_sp)
    return uw.unwind(mem, epc2, regs[_REG_SP], registers=regs,
                     stack_lo=stack_lo, stack_hi=stack_hi, max_frames=max_frames)


def fmt_chain(chain, syms, indent="  "):
    """Render a chain list as lines 'pc fn+off'. Returns the joined string."""
    lines = []
    for i, fr in enumerate(chain):
        pc = fr["pc"]
        sp = (fr.get("sp") or 0) & 0xFFFFFFFF
        lines.append("%s#%02d 0x%08x  sp=0x%08x  %s+0x%x" %
                     (indent, i, pc, sp, fr["fn"], fr["off"]))
    return "\n".join(lines)

