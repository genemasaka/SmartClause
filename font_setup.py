import os
from pathlib import Path
import shutil

def setup_fonts():
    """Set up and verify fonts directory"""
    # Get project root directory
    project_dir = Path(__file__).parent
    fonts_dir = project_dir / 'fonts'
    
    # Create fonts directory if it doesn't exist
    fonts_dir.mkdir(exist_ok=True)
    
    # Expected font files
    expected_fonts = {
        'arial.ttf': 'Arial Regular',
        'arialbd.ttf': 'Arial Bold',
        'ariali.ttf': 'Arial Italic',
        'arialbi.ttf': 'Arial Bold Italic'
    }
    
    # Check each font file
    missing_fonts = []
    for font_file, font_name in expected_fonts.items():
        font_path = fonts_dir / font_file
        if not font_path.exists():
            missing_fonts.append(font_file)
            print(f"Missing font: {font_name} ({font_file})")
    
    if missing_fonts:
        print("\nMissing font files. Please ensure all required font files are in the 'fonts' directory:")
        for font in missing_fonts:
            print(f"- {font}")
        print("\nFont files should be named exactly as shown above.")
    else:
        print("All required fonts are present!")
        
    return len(missing_fonts) == 0

if __name__ == "__main__":
    setup_fonts()