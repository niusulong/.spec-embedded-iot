var group__timer__interface__gr =
[
    [ "timer_pwm_config_t", "group__timer__interface__gr.html#structtimer__pwm__config__t", [
      [ "pwmFreq_HZ", "group__timer__interface__gr.html#af8eb9c1c8f4ea0a6db93885290b1e9f6", null ],
      [ "srcClock_HZ", "group__timer__interface__gr.html#a49da6f7bfdc6bb9169d4edb7d5b80d4c", null ],
      [ "dutyCyclePercent", "group__timer__interface__gr.html#adf9851c3ce0b5866a3a936e9fe521854", null ],
      [ "stopOption", "group__timer__interface__gr.html#ab841e7d360cd34c27e5601ccae661aa4", null ]
    ] ],
    [ "timer_config_t", "group__timer__interface__gr.html#structtimer__config__t", [
      [ "clockSource", "group__timer__interface__gr.html#a11bfa4599df1ec0db4ea3eff706b4e4f", null ],
      [ "reloadOption", "group__timer__interface__gr.html#a3fee470c409099281ad29bd3ce073abc", null ],
      [ "captureMode", "group__timer__interface__gr.html#a3c8792c6ddd02832b98561e9ee768006", null ],
      [ "captureEdge", "group__timer__interface__gr.html#a79f61a97123f56a55c959835aea26bcd", null ],
      [ "initValue", "group__timer__interface__gr.html#a04ae846852cae2aa73b782ec1ac26c23", null ],
      [ "match0", "group__timer__interface__gr.html#aa030e77206dc6105fc07b15a5fee926f", null ],
      [ "match1", "group__timer__interface__gr.html#a9ba32b2ddf6b2388ac4b99031ed54354", null ],
      [ "match2", "group__timer__interface__gr.html#a11a75157b598acd52c5514f619a8153b", null ]
    ] ],
    [ "timer_capture_result_t", "group__timer__interface__gr.html#structtimer__capture__result__t", [
      [ "counterValue", "group__timer__interface__gr.html#ac888c22e9908fbb7cf75b7f73e31440e", null ],
      [ "capturedEdge", "group__timer__interface__gr.html#a42d66c3817d6ff0d2c529e049642c7fd", null ]
    ] ],
    [ "timer_clock_source_t", "group__timer__interface__gr.html#ga02e2977174081f673d6cbc39b7e4d944", [
      [ "TIMER_InternalClock", "group__timer__interface__gr.html#gga02e2977174081f673d6cbc39b7e4d944afc425c500848ae6c7090201141f559d3", null ],
      [ "TIMER_ExternalClock", "group__timer__interface__gr.html#gga02e2977174081f673d6cbc39b7e4d944aa1ba331c1360aaed683b235a091f88e9", null ]
    ] ],
    [ "timer_match_select_t", "group__timer__interface__gr.html#ga782b61985ed8e0b0ba6975ed81d153b7", [
      [ "TIMER_Match0Select", "group__timer__interface__gr.html#gga782b61985ed8e0b0ba6975ed81d153b7a440e668f58988fa27587d2e1ec02ae56", null ],
      [ "TIMER_Match1Select", "group__timer__interface__gr.html#gga782b61985ed8e0b0ba6975ed81d153b7af1f86a40145d6dc90836ed710e6f67af", null ],
      [ "TIMER_Match2Select", "group__timer__interface__gr.html#gga782b61985ed8e0b0ba6975ed81d153b7a6be0ec966dc0e32e178745d7bfc311f6", null ]
    ] ],
    [ "timer_reload_option_t", "group__timer__interface__gr.html#ga60964ea5a0892fbf3a248def7769da2b", [
      [ "TIMER_ReloadDisabled", "group__timer__interface__gr.html#gga60964ea5a0892fbf3a248def7769da2ba45d4c6c22f131f2c96a26f5638f4410d", null ],
      [ "TIMER_ReloadOnMatch0", "group__timer__interface__gr.html#gga60964ea5a0892fbf3a248def7769da2ba435399ba2679ec0375783c6454f7e8b1", null ],
      [ "TIMER_ReloadOnMatch1", "group__timer__interface__gr.html#gga60964ea5a0892fbf3a248def7769da2ba9b995fb9b0c762bb28edd86f1821e8b5", null ],
      [ "TIMER_ReloadOnMatch2", "group__timer__interface__gr.html#gga60964ea5a0892fbf3a248def7769da2ba89f25975df1c8f5a8949835749cfa981", null ]
    ] ],
    [ "timer_capture_mode_control_t", "group__timer__interface__gr.html#ga5763fa5f9668f30927c9252eda09560d", [
      [ "TIMER_CaptureModeDisable", "group__timer__interface__gr.html#gga5763fa5f9668f30927c9252eda09560da7c0e94a833b96107d4cba7053e32a691", null ],
      [ "TIMER_CaptureModeEnable", "group__timer__interface__gr.html#gga5763fa5f9668f30927c9252eda09560da3a4b2943b008993bccd47371269b9e08", null ]
    ] ],
    [ "timer_capture_edge_t", "group__timer__interface__gr.html#gacd2bffcb3c1d1e960841c6afc2d8d51a", [
      [ "TIMER_CaptureRisingEdge", "group__timer__interface__gr.html#ggacd2bffcb3c1d1e960841c6afc2d8d51aab6e2644f0a9380118ed6f1e32631af89", null ],
      [ "TIMER_CaptureFallingEdge", "group__timer__interface__gr.html#ggacd2bffcb3c1d1e960841c6afc2d8d51aaca4937853ed57a08909353b54fa8fd62", null ],
      [ "TIMER_CaptureBothEdge", "group__timer__interface__gr.html#ggacd2bffcb3c1d1e960841c6afc2d8d51aabd814d240be72fee041d0484ebba6bee", null ]
    ] ],
    [ "timer_pwm_stop_option_t", "group__timer__interface__gr.html#ga29ecf3c5f5d6711ee7c5c3dad58a4c27", [
      [ "TIMER_PwmStopLow", "group__timer__interface__gr.html#gga29ecf3c5f5d6711ee7c5c3dad58a4c27aa8b63956ab8e90a33f4e243bad8234da", null ],
      [ "TIMER_PwmStopHigh", "group__timer__interface__gr.html#gga29ecf3c5f5d6711ee7c5c3dad58a4c27af8738b0a888c7cd1e2ea51cdff3e071e", null ],
      [ "TIMER_PwmStopHold", "group__timer__interface__gr.html#gga29ecf3c5f5d6711ee7c5c3dad58a4c27adc893528510c84dd26fefb216b61cd50", null ]
    ] ],
    [ "timer_interrupt_config_t", "group__timer__interface__gr.html#ga33b960d445441e9cecc4a1c4f52f7b53", [
      [ "TIMER_InterruptDisabled", "group__timer__interface__gr.html#gga33b960d445441e9cecc4a1c4f52f7b53a425a13fe5a658390849475a643f9003c", null ],
      [ "TIMER_InterruptLevel", "group__timer__interface__gr.html#gga33b960d445441e9cecc4a1c4f52f7b53a1d8215ad0bfa786c464ea56e4ba4fc60", null ],
      [ "TIMER_InterruptPulse", "group__timer__interface__gr.html#gga33b960d445441e9cecc4a1c4f52f7b53a9bd81fde20a671dad8c8f3038112e1ff", null ]
    ] ],
    [ "timer_interrupt_enable_t", "group__timer__interface__gr.html#gada2ec4ecb7ed118f07b98a795b48f25c", [
      [ "TIMER_Match0InterruptEnable", "group__timer__interface__gr.html#ggada2ec4ecb7ed118f07b98a795b48f25caf690eea7e5bc534e2cf8f31a4d98fb4c", null ],
      [ "TIMER_Match1InterruptEnable", "group__timer__interface__gr.html#ggada2ec4ecb7ed118f07b98a795b48f25ca85a5d6b3338c52fa113849054ac63615", null ],
      [ "TIMER_Match2InterruptEnable", "group__timer__interface__gr.html#ggada2ec4ecb7ed118f07b98a795b48f25ca1c3e20ee55a7c5edb25ad100e0dfd9ae", null ],
      [ "TIMER_CaptureInterruptEnable", "group__timer__interface__gr.html#ggada2ec4ecb7ed118f07b98a795b48f25ca483532b235fc663b4b2155e205d75f23", null ]
    ] ],
    [ "timer_interrupt_flags_t", "group__timer__interface__gr.html#ga617d1941e9bfca0ed1138e9e4cdbae8d", [
      [ "TIMER_Match0InterruptFlag", "group__timer__interface__gr.html#gga617d1941e9bfca0ed1138e9e4cdbae8daacf3925e6fc7309a65393b619c5db620", null ],
      [ "TIMER_Match1InterruptFlag", "group__timer__interface__gr.html#gga617d1941e9bfca0ed1138e9e4cdbae8dad85877b2b4be6fb8831c01d97f3da981", null ],
      [ "TIMER_Match2InterruptFlag", "group__timer__interface__gr.html#gga617d1941e9bfca0ed1138e9e4cdbae8da4364431c4ad434377fef54d914412c28", null ],
      [ "TIMER_CpatureInterruptFlag", "group__timer__interface__gr.html#gga617d1941e9bfca0ed1138e9e4cdbae8dab94f441e0cce755144203c0b05f75682", null ]
    ] ],
    [ "TIMER_DriverInit", "group__timer__interface__gr.html#ga6a6764f551568083e339f0c6c6d196d0", null ],
    [ "TIMER_GetDefaultConfig", "group__timer__interface__gr.html#ga2d65e6cc346c69d99168143730141e0c", null ],
    [ "TIMER_Init", "group__timer__interface__gr.html#ga8cfa3c483ba91ca16f65483e01edc67e", null ],
    [ "TIMER_DeInit", "group__timer__interface__gr.html#ga815284766984cf9b3eddb0b1bccfa6f5", null ],
    [ "TIMER_SetMatch", "group__timer__interface__gr.html#ga208be1445bbdeb78ab9a5d3d8dd8376e", null ],
    [ "TIMER_SetInitValue", "group__timer__interface__gr.html#gaf52085274be1d8c105aca21f2068c35a", null ],
    [ "TIMER_SetReloadOption", "group__timer__interface__gr.html#ga71c65d8deafe3585c8d8303cb342e425", null ],
    [ "TIMER_SetExternalEdgeCountType", "group__timer__interface__gr.html#ga5e9e7e4d6e8e44f31866ea05a6f26479", null ],
    [ "TIMER_SetExternalClockInput", "group__timer__interface__gr.html#ga5ad7a6798cc7399851e29d5435183b71", null ],
    [ "TIMER_Start", "group__timer__interface__gr.html#ga634c56d54b8d561f38dbdc883f88a980", null ],
    [ "TIMER_Stop", "group__timer__interface__gr.html#ga791b78cba39b08f8a7e0c20397d46ac3", null ],
    [ "TIMER_GetCount", "group__timer__interface__gr.html#gac624ec58f89db40344b8a89eb4462cd9", null ],
    [ "TIMER_GetCaptureResult", "group__timer__interface__gr.html#ga91c0a4dd6458079e436d20779ae5e785", null ],
    [ "TIMER_SetupPwm", "group__timer__interface__gr.html#ga860fb37662a91325eb627688b421d536", null ],
    [ "TIMER_UpdatePwmDutyCycle", "group__timer__interface__gr.html#gadce44b65a93b940da28e400130f9409f", null ],
    [ "TIMER_InterruptConfig", "group__timer__interface__gr.html#ga66a9f7c5d73788eddfa16dc2398ac55f", null ],
    [ "TIMER_GetInterruptConfig", "group__timer__interface__gr.html#ga72a10da8d0806fa65af633b2450afcc7", null ],
    [ "TIMER_GetInterruptFlags", "group__timer__interface__gr.html#gadff7463e9e0d5209be5b39fa1ad5f479", null ],
    [ "TIMER_ClearInterruptFlags", "group__timer__interface__gr.html#ga01a00f6e964e6f4c80d9c84d149c52b2", null ],
    [ "TIMER_Netlight_Enable", "group__timer__interface__gr.html#ga5090264395824aef02740494be5c96f4", null ]
];