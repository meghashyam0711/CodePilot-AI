import re

def normalize_grade(grade_str: str) -> str:
    """
    Canonically map grade strings.
    Examples: '10', '10th', '10TH', 'class 10', 'Class X' -> '10'
    """
    if not grade_str:
        return ""
        
    g = grade_str.lower().strip()
    
    # Check for direct number match
    match = re.search(r"(\d+)", g)
    if match:
        val = int(match.group(1))
        if 1 <= val <= 12:
            return str(val)
            
    # Check for roman numerals
    roman_map = {
        "viii": 8, "vii": 7, "iii": 3, "vii": 7, "viii": 8,
        "xii": 12, "xi": 11, "iii": 3, "vii": 7, "viii": 8,
        "x": 10, "ix": 9, "vi": 6, "v": 5, "iv": 4, "ii": 2, "i": 1
    }
    
    for rom, val in roman_map.items():
        if re.search(rf"\b{rom}\b", g):
            return str(val)
            
    # Check for word numbers
    word_map = {
        "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5,
        "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10,
        "eleventh": 11, "twelfth": 12
    }
    for w, val in word_map.items():
        if w in g:
            return str(val)
            
    return grade_str.strip()
