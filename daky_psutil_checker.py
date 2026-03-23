#!/usr/bin/env python3

"""
System Monitor Troubleshooter - Run this to identify startup errors
"""

import os
import sys
import traceback
import logging

# Set up error logging to a file
os.makedirs(os.path.expanduser("~/system_monitor_debug"), exist_ok=True)
log_file = os.path.expanduser("~/system_monitor_debug/error.log")

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s',
    filename=log_file,
    filemode='w'
)

def main():
    try:
        print("Starting troubleshooter...")
        logging.info("Script started")
        
        # Try importing required libraries to see what might be missing
        print("Checking imports...")
        
        try:
            import psutil
            print("✓ psutil imported successfully")
            logging.info("psutil import successful")
        except ImportError as e:
            print(f"✗ ERROR: Could not import psutil: {e}")
            print("  Try installing with: pip install psutil")
            logging.error(f"psutil import failed: {e}")
            return
        
        try:
            from rich import box
            from rich.text import Text
            from rich.panel import Panel
            print("✓ rich imported successfully")
            logging.info("rich import successful")
        except ImportError as e:
            print(f"✗ ERROR: Could not import rich: {e}")
            print("  Try installing with: pip install rich")
            logging.error(f"rich import failed: {e}")
            return
        
        try:
            from textual.app import App
            print("✓ textual imported successfully")
            logging.info("textual import successful")
        except ImportError as e:
            print(f"✗ ERROR: Could not import textual: {e}")
            print("  Try installing with: pip install textual")
            logging.error(f"textual import failed: {e}")
            return
            
        # Try to load configuration
        try:
            import yaml
            print("✓ yaml imported successfully")
            logging.info("yaml import successful")
        except ImportError as e:
            print(f"✗ ERROR: Could not import yaml: {e}")
            print("  Try installing with: pip install pyyaml")
            logging.error(f"yaml import failed: {e}")
            return
        
        # Now try loading the original script to see where it fails
        print("\nAttempting to start the system monitor...")
        logging.info("Attempting to start the full system monitor")
        
        # This will execute the original script code but in a controlled way
        exec(open("psutil_2fff.py").read())
        
    except Exception as e:
        error_details = traceback.format_exc()
        print(f"\n\n{'='*50}")
        print(f"ERROR: The system monitor crashed with the following error:")
        print(f"{type(e).__name__}: {e}")
        print(f"\nDetailed traceback:")
        print(error_details)
        print(f"{'='*50}")
        print(f"\nThis error has been saved to: {log_file}")
        print("Press ENTER to exit...")
        
        # Log the error
        logging.error(f"Script crashed: {e}")
        logging.error(f"Traceback: {error_details}")
        
        # Wait for user to acknowledge before exiting
        input()

if __name__ == "__main__":
    # Prevent the terminal from being restored on exit
    os.environ['TEXTUAL_RESTORE_TERMINAL'] = "0"
    
    # Run the troubleshooter
    main()
    
    # Keep the terminal open
    print("\nTroubleshooting complete. Press ENTER to exit...")
    input()