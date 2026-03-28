def best_account_match(accounts, search_name):
    """
    Given a list of Zoho accounts and a search name, return the best matching account.
    
    Account names are formatted like "מבטחים - אילן", "עין הבשור - אילן סנג".
    When user searches "אילן", we want "מבטחים - אילן" (exact match on the landlord part)
    not "עין הבשור - אילן סנג" (partial match).
    
    Priority:
    1. Exact match on landlord part (after " - ")
    2. Exact match on full account name
    3. Shortest name that contains the search term (most specific)
    """
    if not accounts:
        return None
    if len(accounts) == 1:
        return accounts[0]
    
    search_lower = search_name.strip().lower()
    
    # Priority 1: Exact match on landlord part (after " - ")
    for a in accounts:
        name = a.get("Account_Name", "")
        if " - " in name:
            landlord_part = name.split(" - ", 1)[1].strip().lower()
            if landlord_part == search_lower:
                return a
    
    # Priority 2: Exact match on full account name
    for a in accounts:
        name = a.get("Account_Name", "").lower()
        if name == search_lower:
            return a
    
    # Priority 3: Filter to those containing the search term, pick shortest (most specific)
    containing = [a for a in accounts if search_lower in a.get("Account_Name", "").lower()]
    if containing:
        containing.sort(key=lambda a: len(a.get("Account_Name", "")))
        return containing[0]
    
    # Fallback: first result
    return accounts[0]
