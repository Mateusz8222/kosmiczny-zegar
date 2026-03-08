Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = "C:\Users\witam\OneDrive\Pulpit\pogaduchy bot"
WshShell.Run """C:\Users\witam\AppData\Local\Programs\Python\Python312\pythonw.exe"" ""C:\Users\witam\OneDrive\Pulpit\pogaduchy bot\bot.py""", 0, False
Set WshShell = Nothing