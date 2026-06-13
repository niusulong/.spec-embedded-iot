var group__dma__interface__gr =
[
    [ "Status Error Codes", "group__dma__execution__status.html", "group__dma__execution__status" ],
    [ "DMA Events", "group__DMA__events.html", "group__DMA__events" ],
    [ "dma_transfer_config", "group__dma__interface__gr.html#structdma__transfer__config", [
      [ "sourceAddress", "group__dma__interface__gr.html#af58b680ebd9b5dda8024987efa6f7d84", null ],
      [ "targetAddress", "group__dma__interface__gr.html#af36a7ad7c743e7771b8ada4b1870d8d7", null ],
      [ "flowControl", "group__dma__interface__gr.html#ab43471f9da7bf51e132ddecda7303c1f", null ],
      [ "addressIncrement", "group__dma__interface__gr.html#ac2025f42577767f5c37a3db5cf5b13e0", null ],
      [ "dataWidth", "group__dma__interface__gr.html#ac5c8792db0bc1029320762621994e0b3", null ],
      [ "burstSize", "group__dma__interface__gr.html#a3734e766d972e5b6dd20e81f956787b7", null ],
      [ "totalLength", "group__dma__interface__gr.html#ae01f1521685b8b3d205dae8d01bca481", null ]
    ] ],
    [ "dma_extra_config", "group__dma__interface__gr.html#structdma__extra__config", [
      [ "nextDesriptorAddress", "group__dma__interface__gr.html#a5275d544eec152e50340109c8df47873", null ],
      [ "stopDecriptorFetch", "group__dma__interface__gr.html#a8907ed15fad0561a06ba3390c42ad6f0", null ],
      [ "enableStartInterrupt", "group__dma__interface__gr.html#a83b644a01fc4a5c15940ed0bd318aead", null ],
      [ "enableEndInterrupt", "group__dma__interface__gr.html#a38f85792864a7c31b84a5c18bbd0ed17", null ]
    ] ],
    [ "dma_descriptor_t", "group__dma__interface__gr.html#structdma__descriptor__t", [
      [ "DAR", "group__dma__interface__gr.html#ab409d052e7a184d36b412e665a1e079a", null ],
      [ "SAR", "group__dma__interface__gr.html#a9f71dcd898faa75d4e2d1e8cace3882f", null ],
      [ "TAR", "group__dma__interface__gr.html#a17a2cd556a1ac35305398a9b615155bb", null ],
      [ "CMDR", "group__dma__interface__gr.html#ad9fc73b0e3d61162e85d6adefd1d2271", null ]
    ] ],
    [ "ARM_DMA_ERROR_CHANNEL_ALLOC", "group__dma__interface__gr.html#gadf5d42ca40aa71306e8858adbdac5547", null ],
    [ "DMA_STOP_TIMEOUT", "group__dma__interface__gr.html#gacd7952f07816647858faf6488ac5ce64", null ],
    [ "dma_callback_t", "group__dma__interface__gr.html#ga3922d52154cbd36e19c3c1fa7c6496ee", null ],
    [ "dma_address_increment_t", "group__dma__interface__gr.html#gabe41348883faa39396ca35a438d9db82", [
      [ "DMA_AddressIncrementNone", "group__dma__interface__gr.html#ggabe41348883faa39396ca35a438d9db82a638ab7adec9a3e317b2ae6f56bdd4e83", null ],
      [ "DMA_AddressIncrementSource", "group__dma__interface__gr.html#ggabe41348883faa39396ca35a438d9db82a84dbce7a1c36f0a9d3aec71f5ee18a47", null ],
      [ "DMA_AddressIncrementTarget", "group__dma__interface__gr.html#ggabe41348883faa39396ca35a438d9db82adcd9f0c7d433dd7a9c370050145da04d", null ],
      [ "DMA_AddressIncrementBoth", "group__dma__interface__gr.html#ggabe41348883faa39396ca35a438d9db82a72909dc064dc1188f1f6a7dd5d44b778", null ]
    ] ],
    [ "dma_flow_control_t", "group__dma__interface__gr.html#gae2dbdab22abb885945a13243444bb5b7", [
      [ "DMA_FlowControlNone", "group__dma__interface__gr.html#ggae2dbdab22abb885945a13243444bb5b7a13b632a0dad151278ca1b628a3cbe3f5", null ],
      [ "DMA_FlowControlSource", "group__dma__interface__gr.html#ggae2dbdab22abb885945a13243444bb5b7a4f8bf8590a136acf82034c535576c7d7", null ],
      [ "DMA_FlowControlTarget", "group__dma__interface__gr.html#ggae2dbdab22abb885945a13243444bb5b7a6bce6043c8b0d5efb21a4fe5e2d27e34", null ]
    ] ],
    [ "dma_data_width_t", "group__dma__interface__gr.html#ga0d10903c1db6a2aa72777609ce3e7ec3", [
      [ "DMA_DataWidthNoUse", "group__dma__interface__gr.html#gga0d10903c1db6a2aa72777609ce3e7ec3aa895690efdb6b244c36e655cb770207e", null ],
      [ "DMA_DataWidthOneByte", "group__dma__interface__gr.html#gga0d10903c1db6a2aa72777609ce3e7ec3a0584e85d07bb95e0851c1e9754e1faf6", null ],
      [ "DMA_DataWidthTwoBytes", "group__dma__interface__gr.html#gga0d10903c1db6a2aa72777609ce3e7ec3a9da7fc7d0bd0590f93eac94d3b4e49df", null ],
      [ "DMA_DataWidthFourBytes", "group__dma__interface__gr.html#gga0d10903c1db6a2aa72777609ce3e7ec3adec939c2a43b89ee20692b3b3a5488dd", null ]
    ] ],
    [ "dma_burst_size_t", "group__dma__interface__gr.html#ga270da8c0489b7245e4d1e50ed7dece3b", [
      [ "DMA_Burst4Bytes", "group__dma__interface__gr.html#gga270da8c0489b7245e4d1e50ed7dece3ba59a48f7436b7f1ee95d682f11666d892", null ],
      [ "DMA_Burst8Bytes", "group__dma__interface__gr.html#gga270da8c0489b7245e4d1e50ed7dece3ba649fd52f523f48622cff257a5879ef24", null ],
      [ "DMA_Burst16Bytes", "group__dma__interface__gr.html#gga270da8c0489b7245e4d1e50ed7dece3ba26367de4b41ee61283b6c6ff324afc95", null ],
      [ "DMA_Burst32Bytes", "group__dma__interface__gr.html#gga270da8c0489b7245e4d1e50ed7dece3ba7ad826f0b4ab16b10f3f02cc5cd0275c", null ],
      [ "DMA_Burst64Bytes", "group__dma__interface__gr.html#gga270da8c0489b7245e4d1e50ed7dece3ba88e9eb28465af59f37d9c783bee2bf4f", null ]
    ] ],
    [ "dma_interrupt_enable_t", "group__dma__interface__gr.html#ga1a7fb1856934d97d2f53c4328dffe321", [
      [ "DMA_StopInterruptEnable", "group__dma__interface__gr.html#gga1a7fb1856934d97d2f53c4328dffe321a5c8c66c221f9d2d8c5c35ec0486624fe", null ],
      [ "DMA_EORInterruptEnable", "group__dma__interface__gr.html#gga1a7fb1856934d97d2f53c4328dffe321ad158cec6851a0278395c6541336882bf", null ],
      [ "DMA_StartInterruptEnable", "group__dma__interface__gr.html#gga1a7fb1856934d97d2f53c4328dffe321a096ed29c81d13805b7e170c91c5c4abf", null ],
      [ "DMA_EndInterruptEnable", "group__dma__interface__gr.html#gga1a7fb1856934d97d2f53c4328dffe321ab720e66db93ac4e4639732baabc869ec", null ]
    ] ],
    [ "DMA_Init", "group__dma__interface__gr.html#ga92a2de6fa92b36d1801358ea9b12273b", null ],
    [ "DMA_OpenChannel", "group__dma__interface__gr.html#ga8f097a962d54bbd1625c2c2d7843881c", null ],
    [ "DMA_CloseChannel", "group__dma__interface__gr.html#ga48aab7fbb301c408157b4b0d26a78536", null ],
    [ "DMA_StartChannel", "group__dma__interface__gr.html#gac895f0ca02a65f34e1e66a02a9f62817", null ],
    [ "DMA_StopChannel", "group__dma__interface__gr.html#gae0202de70508d7cb5f4fc79c2be69375", null ],
    [ "DMA_ResetChannel", "group__dma__interface__gr.html#gab9110f7240d048efa865f5d50ba1803b", null ],
    [ "DMA_ChannelRigisterCallback", "group__dma__interface__gr.html#ga450c7eb06ab0f78395390f50a31b92d9", null ],
    [ "DMA_EnableChannelInterrupts", "group__dma__interface__gr.html#ga590526b9492631f1e954b65c4d12c959", null ],
    [ "DMA_DisableChannelInterrupts", "group__dma__interface__gr.html#gae5c1df879126b5ccea627ba1090018a4", null ],
    [ "DMA_GetEnabledInterrupts", "group__dma__interface__gr.html#gab85346e736485a819dc68ffb319acaa8", null ],
    [ "DMA_ChannelGetCount", "group__dma__interface__gr.html#gaea5271772a6db8f7c1ac45eb5c586bbe", null ],
    [ "DMA_ChannelSetRequestSource", "group__dma__interface__gr.html#ga641cdbe1fbe9507301754f433f8e3d21", null ],
    [ "DMA_TransferSetup", "group__dma__interface__gr.html#gae416d0758bb95a29034b47a9558f4f4d", null ],
    [ "DMA_BuildDescriptor", "group__dma__interface__gr.html#ga7c0845db54c2aaa6e4e7fcf3f3b2fa2f", null ],
    [ "DMA_ChannelLoadFirstDescriptor", "group__dma__interface__gr.html#ga5e88d8d9aee40cc81b84bd659e4d1e05", null ],
    [ "DMA_GetActiveStateExceptUnilog", "group__dma__interface__gr.html#ga8ee861abc9bfbd5ea68ceeaf95471f1a", null ],
    [ "DMA_TryDeactiveExceptUnilog", "group__dma__interface__gr.html#ga3ce73e09587cbd7d44395a191152f501", null ]
];