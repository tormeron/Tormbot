# Tormbot
A Bot for twitch for small streamers, for local use only, do not deploy on public networks or servers

This bot has not been secured for public environments, it's meant to be hosted locally on the streaming computer or another computer on a local network. it opens some ports for receiving and sending data to and from an AI program, to allow for interactions with AI models. 

Currently the AI software is not released since it has some major bugs that might cause issues. leave the AI section as off and ports closed if possible for minimal danger. 

I repeat, do NOT host this software on a public facing server or computer, it has not been designed for it!

I would not advise using your own streaming user to be the bot user, keep it seperate and make sure you keep within TOS of twitch. 
This bot has been designed so it will auto detect if it is a mod or not on twitch and has hard limits from spamming the chat, but this has been implemented at a point, later Terms of Service might change and limits might change as well. 

There are higher limits on number of messages for Mods than regular users in regards to bots and posting messages on chat and sending commands to twitch servers.

This has been generated using gemini, since I do not know so much about MAC or Linux in regards to compile process. 
please verify these instructions are correct

This program can run and be compiled on all major operating systems:

    Windows (Windows 10, 11)

    macOS

    Linux (Ubuntu, Debian, Fedora, Arch, etc.)

Because the code is written in standard Python 3 using cross-platform libraries (tkinter, pygame, sqlite3, requests, etc.), it is inherently cross-platform.
OS-Specific Setup Notes
1. Windows

    Status: Ready out-of-the-box.

    Notes: The code uses Segoe UI fonts, which are native to Windows, so the GUI will render natively. Standard Python installers for Windows automatically include tkinter.

2. Linux

    Status: Fully supported.

    Notes: On many Linux distributions, tkinter is not bundled with the base python3 package. You may need to install it via your package manager first:

        Ubuntu/Debian: sudo apt install python3-tk

        Fedora: sudo dnf install python3-tkinter

        Arch Linux: sudo pacman -S tk

3. macOS

    Status: Fully supported.

    Notes: If you installed Python via python.org, Tkinter is included automatically. If installed via Homebrew, you may need to run brew install python-tk.

If You Want to "Compile" It into a Standalone Executable (.exe / .app)

Because Python is an interpreted language, "compiling" typically refers to packaging the script into a single executable file so users can run it without installing Python.

You can use PyInstaller on any OS:

    Install PyInstaller:
    Bash

    pip install pyinstaller

    Build the executable:
    Bash

    pyinstaller --noconsole --onefile Main.py

    Building on Windows creates a Windows .exe file.

    Building on macOS creates a macOS executable/app.

    Building on Linux creates a Linux ELF binary.

(Note: PyInstaller is not a cross-compiler—to generate a Windows .exe, you must run PyInstaller on a Windows system).