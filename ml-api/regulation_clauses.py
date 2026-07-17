"""
Chunked SBP AML/CFT/CPF Regulation clauses used to ground every LLM-drafted
compliance document in UC7 (and cross-referenced use cases).

In production, replace this static list with a Chroma collection populated
by chunking the full Regulations PDF (Part C, by Regulation/section). Kept
as a plain Python list here so the /api/vector-db/query endpoint is fully
offline-testable without a running vector DB.
"""

CLAUSES = [
    {
        "id": "REG6-S1",
        "citation": "Regulation 6, Section 1 (SBP AML/CFT/CPF Regulations)",
        "text": (
            "SBP REs shall conduct Enhanced Due Diligence (EDD), including obtaining "
            "senior management approval, while establishing a relationship or executing "
            "a financial transaction with NGOs/NPOs, Charities and Trusts, when the risks "
            "are higher, to ensure these accounts are used for legitimate purposes and "
            "that transactions are commensurate with the stated objectives and purposes."
        ),
    },
    {
        "id": "REG6-S2",
        "citation": "Regulation 6, Section 2 (SBP AML/CFT/CPF Regulations)",
        "text": (
            "When onboarding or opening an account/financial service in the name of an "
            "NGO/NPO, SBP REs shall ensure the account is opened in the name of the "
            "relevant NGO/NPO exactly as per the title given in its constituent documents."
        ),
    },
    {
        "id": "REG6-S3",
        "citation": "Regulation 6, Section 3 (SBP AML/CFT/CPF Regulations)",
        "text": (
            "Individuals authorized to operate NGO/NPO/Trust accounts and all members of "
            "their governing body shall be subject to CDD separately, and SBP REs shall "
            "ensure such persons are not affiliated with any Designated Person (DP) or "
            "Proscribed Person (PP), whether under the same name or a different name."
        ),
    },
    {
        "id": "REG6-S4",
        "citation": "Regulation 6, Section 4 (SBP AML/CFT/CPF Regulations)",
        "text": (
            "Where an advertisement (newspaper or other medium) solicits donations and "
            "mentions a bank account number or customer relationship number, the RE shall "
            "ensure that the title of the account/relationship matches the title of the "
            "entity soliciting the donations. In case of any difference, immediate caution "
            "shall be marked on the account/relationship and the matter shall be considered "
            "for filing a Suspicious Transaction Report (STR)."
        ),
    },
    {
        "id": "REG6-S5",
        "citation": "Regulation 6, Section 5 (SBP AML/CFT/CPF Regulations)",
        "text": (
            "Personal accounts/customer relationships shall not be allowed to be used for "
            "charity purposes or for the collection of donations."
        ),
    },
    {
        "id": "REG6-S6",
        "citation": "Regulation 6, Section 6 (SBP AML/CFT/CPF Regulations)",
        "text": (
            "All existing NGO/NPO/Charity/Trust relationships shall be reviewed and "
            "monitored to ensure their authorized signatories, governing body members, "
            "beneficial owners, or trustees are not linked with any DP or PP under the "
            "same or a different name. Any positive match shall be considered for STR "
            "filing and/or other action as per law."
        ),
    },
    {
        "id": "REG7-S1",
        "citation": "Regulation 7, Section 1 (SBP AML/CFT/CPF Regulations)",
        "text": (
            "SBP Regulated Entities shall file Suspicious Transaction Reports (STRs) and "
            "Currency Transaction Reports (CTRs) with the Financial Monitoring Unit (FMU) "
            "as required under Section 7 of the Anti-Money Laundering Act, 2010."
        ),
    },
    {
        "id": "REG5-S1",
        "citation": "Regulation 5, Section 1 (SBP AML/CFT/CPF Regulations)",
        "text": (
            "For PEPs, close associates, and family members, REs shall determine PEP "
            "status before and throughout a business relationship, obtain senior "
            "management approval to establish or continue the relationship, establish "
            "source of wealth/funds, and conduct enhanced ongoing monitoring."
        ),
    },
]


def keyword_hits(query: str, clause_text: str) -> int:
    q_tokens = set(query.lower().split())
    c_tokens = set(clause_text.lower().split())
    return len(q_tokens & c_tokens)
