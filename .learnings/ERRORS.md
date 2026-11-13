# Errors

## [ERR-20261113-001] osascript simulator input

**Logged**: 2026-11-13T11:20:00+03:00
**Priority**: medium
**Status**: resolved
**Area**: frontend

### Summary
AppleScript cannot read or drive the iOS simulator window, so UI screenshots could not be scrolled or tapped programmatically.

### Error
```
execution error: System Events got an error: Can’t get process "Simulator". (-1728)
execution error: System Events got an error: osascript is not allowed assistive access. (-1728)
```

### Context
- Command: `osascript -e 'tell application "System Events" to tell process "Simulator" to get {position, size} of window 1'`
- Environment: Xcode 27.2, iOS 27.2 simulators running headless; GUI process is "DeviceHub", not "Simulator"
- A CGEvent scroll helper built with Swift also found no window: `CGWindowListCopyWindowInfo(.optionOnScreenOnly)` listed only Qoder windows.

### Suggested Fix
Do not automate simulator input. Use `simctl` screenshots plus persisted defaults, a tall device for long pages, and the temporary-root technique in LRN-20261113-002 when a screen is unreachable.

### Resolution
- **Resolved**: 2026-11-13T11:30:00+03:00
- **Notes**: All remaining surfaces were verified via defaults preset + relaunch, an iPad screenshot, and a temporary env-var root with a truncating stub; the hook was removed and `grep` confirmed clean.

### Metadata
- Reproducible: yes
- Related Files: ios/FIE/FIEApp.swift
- See Also: LRN-20261113-001, LRN-20261113-002

---
