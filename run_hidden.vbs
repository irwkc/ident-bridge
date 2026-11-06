' Starts bridge with no console window.
Set sh = CreateObject("WScript.Shell")
sh.CurrentDirectory = "C:\ident-bridge"
sh.Run "cmd.exe /c C:\ident-bridge\run_forever.cmd", 0, False
