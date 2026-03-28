# 預設 False；若沒有接 LCD、想直接進入 Wi-Fi 設定入口，改為 True。
FORCE_HEADLESS = True

# 開機時自動開 AP (PicoSetup/pico1234) + HTTP 設定頁，方便手機設定 Wi-Fi。
# 若不需要可設 False。
# 注意：此值主要影響「啟動時機」；main.py 仍有保底補啟 AP/服務邏輯。
AUTO_CONFIG_AP_ON_BOOT = True
