# Priority send-type emoji crops

These five PNG files are exact crops from the supplied master image. Their artwork has not been redrawn or restyled.

Upload them as application emojis in the Discord Developer Portal with these names:

| Send type | File | Application emoji name |
| --- | --- | --- |
| Rate | `pps_rate.png` | `pps_rate` |
| Feature | `pps_feature.png` | `pps_feature` |
| Epic | `pps_epic.png` | `pps_epic` |
| Legendary | `pps_legendary.png` | `pps_legendary` |
| Mythic | `pps_mythic.png` | `pps_mythic` |

After uploading, copy each emoji ID into `priority_system.send_type_emojis` in `config.json` and restart Avenue Guard. Empty IDs are supported; the menu simply omits its icons until they are configured.
