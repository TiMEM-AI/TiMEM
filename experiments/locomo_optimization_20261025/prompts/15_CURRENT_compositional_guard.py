def compositional_query(query):
    if re.match(r'\s*(?:when\b|(?:what|which)\s+(?:date|day|month|year|week)\b|how\s+long\b)',query,re.I):return False
    return bool(re.search(r'\b(?:both|common|together|compare|comparison|besides|other|all|list|and|why)\b'
        r'|\b(?:what\s+(?:are|kinds?|types?|\w+s)|which\b|how\s+many\b)',query,re.I))
