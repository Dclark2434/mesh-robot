# Audio Configuration Verification

We have switched to using PipeWire with an RNNoise filter for microphone input. The `MESH_ALSA_DEVICE` variable has been removed, so the system relies on the default PipeWire source.

## Verification

To verify that the noise cancellation is working and selected as the default:

1.  **Check Status**:
    Run `wpctl status` and look for the asterisk `*` next to the source. It should be the `rnnoise_source` or similar virtual sink/source, not the raw hardware.
    ```bash
    wpctl status
    ```

2.  **Record Test**:
    Record a short clip using `pw-record` (which uses the default PipeWire source) and play it back.
    ```bash
    pw-record test.wav
    # (Talk, then Ctrl+C)
    pw-play test.wav
    ```

## Troubleshooting

If `wpctl status` shows the wrong default:
1.  Find the ID of the `rnnoise_source` in the list.
2.  Set it as default:
    ```bash
    wpctl set-default <ID>
    ```

Also make sure your preferred output device is set as default using the same method.
