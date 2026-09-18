$TaskName = "INNERBALANCE_BOT_NEWS_WATCHDOG"
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
"uninstalled $TaskName"
