/**
 * spec-embedded-iot — Pi session bootstrap extension.
 *
 * Mirrors the Claude Code SessionStart hook (hooks/session-start): on the first
 * agent turn of each session it injects the `spec-using-agents` SKILL.md as
 * hidden LLM context, so the model knows the skill-entry / routing rules from
 * turn 1. The skills themselves are discovered natively by Pi (every
 * skills/<name>/SKILL.md is surfaced in the system prompt); this extension only
 * pre-loads the meta-skill routing content.
 *
 * Loaded by Pi via the package's `pi.extensions` field. TypeScript is loaded
 * directly via jiti (no build step). The core-package import below is type-only,
 * so it is erased at runtime; it is declared as an OPTIONAL peerDependency so
 * other platforms (OpenCode / Codex / Cursor) that install this repo are
 * unaffected.
 *
 * @see https://pi.dev/docs/latest/extensions  (before_agent_start, session_start)
 */
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const BOOTSTRAP_TYPE = "spec-using-agents-bootstrap";

// Package root = parent of this extensions/ directory. Resolved from the
// extension's own URL so it works regardless of the user's project cwd.
let pkgRoot = "";
try {
  pkgRoot = dirname(fileURLToPath(import.meta.url));
} catch {
  // Non-ESM loader without import.meta — disable injection; skills still load natively.
  pkgRoot = "";
}

function buildContext(): string | null {
  if (!pkgRoot) return null;
  let skill: string;
  try {
    skill = readFileSync(
      join(pkgRoot, "..", "skills", "spec-using-agents", "SKILL.md"),
      "utf8",
    );
  } catch {
    // Skill file missing (partial install / renamed). Skip silently — Pi still
    // surfaces every skill natively, so functionality is unaffected.
    return null;
  }
  return (
    "<EXTREMELY_IMPORTANT>\n" +
    "You have access to embedded IoT development skills and a persistent knowledge base.\n\n" +
    "**Below is the full content of your 'spec-using-agents' skill - your guide to using all skills. " +
    "For other skills, invoke `/skill:<name>` (Pi loads all skills into your context natively).**\n\n" +
    skill +
    "\n</EXTREMELY_IMPORTANT>"
  );
}

type SessionEntry = { type?: string; message?: { customType?: string } };

export default function (pi: ExtensionAPI): void {
  // Reset once per session start: startup / new / resume / fork / reload all
  // either create a fresh extension instance or re-fire session_start.
  let injected = false;
  pi.on("session_start", () => {
    injected = false;
  });

  pi.on("before_agent_start", async (_event, ctx) => {
    if (injected) return;
    injected = true;

    // De-dup: on /reload the previous bootstrap message is still in the branch.
    const alreadyBootstrapped = ctx.sessionManager
      .getBranch()
      .some(
        (entry) =>
          (entry as SessionEntry).type === "message" &&
          (entry as SessionEntry).message?.customType === BOOTSTRAP_TYPE,
      );
    if (alreadyBootstrapped) return;

    const content = buildContext();
    if (!content) return;

    // Persistent (stored in session), LLM-visible, TUI-hidden context message.
    return {
      message: { customType: BOOTSTRAP_TYPE, content, display: false },
    };
  });
}
