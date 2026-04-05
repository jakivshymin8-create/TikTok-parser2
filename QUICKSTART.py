#!/usr/bin/env python3
"""
TikTok Parser Quick Start Guide

This file contains quick reference for running the TikTok parser.
"""

import os
import sys

# Add current directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

WELCOME_TEXT = r"""
╔════════════════════════════════════════════════════════════════╗
║                                                                ║
║           TikTok Parser - Quick Start Guide                   ║
║                                                                ║
╚════════════════════════════════════════════════════════════════╝
"""

INSTRUCTIONS = """
STEP 1: Activate Virtual Environment
  PowerShell:  .\venv\Scripts\Activate.ps1
  CMD:         venv\Scripts\activate.bat
  Linux/Mac:   source venv/bin/activate

STEP 2: Run the Program
  python main.py

STEP 3: Follow Console Instructions
  1. Wait for your Chrome to open
  2. Go to www.tiktok.com
  3. Login if needed
  4. Press Enter in console

STEP 4: Wait for Completion
  Program will:
  - Close Chrome
  - Show results (if any)
  - Exit cleanly
"""

PROJECT_STRUCTURE = """
Project Structure:
  main.py              - Main program, run this
  src/
    browser.py         - Opens your Chrome with Default profile
    scroll.py          - Scroll TikTok (not integrated yet)
    parser.py          - Parse videos (not integrated yet)
  data/                - Where results will be saved
  venv/                - Python virtual environment

Configuration:
  Chrome Path:         C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe
  Chrome Profile:      Default (your personal profile)
  Chrome User Data:    C:\\Users\\jakiv\\AppData\\Local\\Google\\Chrome\\User Data
"""

TROUBLESHOOTING = """
If Chrome Doesn't Open:
  1. Make sure Chrome is installed
  2. Check if path is correct in src/browser.py
  3. Close all Chrome windows
  4. Run: Get-Process chrome | Stop-Process
  5. Try again

If Program Hangs:
  1. Open Task Manager (Ctrl+Shift+Esc)
  2. Find Chrome in processes
  3. End task
  4. In console, press Ctrl+C to stop program
  5. Try again

If Import Error (ImportError: No module named 'src'):
  1. Make sure __init__.py exists in src/ folder
  2. Make sure you're in tiktok_parser folder
  3. Make sure venv is activated
"""

def print_section(title, content):
    """Print a formatted section"""
    print()
    print("=" * 70)
    print(f"{title.upper()}")
    print("=" * 70)
    print(content)
    print()

if __name__ == "__main__":
    print(WELCOME_TEXT)
    
    print_section("Quick Start Instructions", INSTRUCTIONS)
    print_section("Project Structure", PROJECT_STRUCTURE)
    print_section("Troubleshooting", TROUBLESHOOTING)
    
    print("=" * 70)
    print("Ready to start? Run: python main.py")
    print("=" * 70)
    print()
