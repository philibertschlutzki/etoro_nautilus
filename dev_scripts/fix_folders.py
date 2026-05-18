import os

tick_dir = "data/nautilus/data/quote_tick"

if os.path.exists(tick_dir):
    for folder in os.listdir(tick_dir):
        if os.path.isdir(os.path.join(tick_dir, folder)) and not folder.startswith("instrument_id="):
            old_path = os.path.join(tick_dir, folder)
            new_path = os.path.join(tick_dir, f"instrument_id={folder}")
            os.rename(old_path, new_path)
            print(f"✅ Repariert: {folder} -> instrument_id={folder}")