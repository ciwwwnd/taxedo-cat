from __future__ import annotations

TAX_CATEGORIES = {
    "Werbungskosten": {
        "label": "Employment expenses",
        "description": "Income-related expenses (employment)",
        "subcategories": {
            "Fahrtkosten / Entfernungspauschale": {
                "label": "Commuting expenses",
                "description": (
                    "Commuting costs between home and the first workplace "
                    "(distance allowance, one way)"
                ),
                "examples": [
                    "Public transport tickets",
                    "Car fuel for commute",
                    "Bicycle for commute",
                ],
                "retrieval_aliases": [
                    "öffentliche Verkehrsmittel",
                    "Arbeitsweg",
                    "erste Tätigkeitsstätte",
                ],
            },
            "Arbeitsmittel": {
                "label": "Work equipment",
                "description": "Work equipment and tools",
                "examples": [
                    "Laptop / Computer",
                    "Monitor / peripherals",
                    "Software licenses",
                    "Office supplies",
                    "Professional books",
                    "Tools for work",
                    "Work bag / briefcase",
                    "Work clothing (if specific)",
                ],
                "retrieval_aliases": [
                    "Bildschirm",
                    "Tastatur",
                    "Werkzeug",
                    "Berufsbekleidung",
                ],
            },
            "Homeoffice-Pauschale": {
                "label": "Working from home allowance",
                "description": "Home office flat rate per day worked from home",
                "examples": ["Working from home days"],
            },
            "Arbeitszimmer": {
                "label": "Dedicated home office",
                "description": (
                    "Dedicated home office room (if center of professional activity) "
                ),
                "examples": [
                    "Rent proportion",
                    "Utilities proportion",
                    "Office furniture",
                ],
            },
            "Fortbildungskosten": {
                "label": "Professional training",
                "description": (
                    "Professionally related further education or second-degree "
                    "study after a completed first vocational qualification or "
                    "university degree; generally Werbungskosten when connected "
                    "to current or intended work"
                ),
                "examples": [
                    "Course fees",
                    "Seminars",
                    "Conferences",
                    "Professional certifications",
                    "Study materials",
                    "Exam fees",
                    "Travel to training",
                    "Software subscriptions used for professional study",
                    "Online learning platforms and academic databases",
                ],
                "retrieval_aliases": [
                    "Weiterbildung",
                    "berufliche Qualifikation",
                    "Fortbildung",
                ],
            },
            "Bewerbungskosten": {
                "label": "Job application expenses",
                "description": "Job application costs",
                "examples": [
                    "Application photos",
                    "Copies",
                    "Postage",
                    "Travel to interviews",
                    "Professional CV service",
                ],
            },
            "Doppelte Haushaltsführung": {
                "label": "Maintaining a second household",
                "description": "Double household costs (if work requires second home)",
                "examples": [
                    "Second apartment rent",
                    "Moving costs",
                    "Weekly trips home",
                    "Household items for second home",
                ],
            },
            "Reisekosten (Dienstreisen)": {
                "label": "Business travel",
                "description": "Business travel expenses",
                "examples": [
                    "Train/flight tickets",
                    "Hotel",
                    "Rental car",
                    "Meals (Verpflegungsmehraufwand)",
                    "Parking fees during business travel",
                ],
            },
            "Kontoführungsgebühren": {
                "label": "Bank account fees",
                "description": "Bank account fees",
                "examples": ["Bank fees for salary account"],
            },
            "Telefon / Internet": {
                "label": "Phone and internet",
                "description": "Phone and internet for work use",
                "examples": ["Mobile phone bill", "Internet bill", "Work phone"],
                "retrieval_aliases": [
                    "Telefonrechnung",
                    "Internetrechnung",
                ],
            },
            "Gewerkschaftsbeiträge": {
                "label": "Trade union dues",
                "description": "Trade union membership fees",
                "examples": ["Union dues"],
            },
            "Berufsverbände": {
                "label": "Professional association dues",
                "description": "Professional association fees",
                "examples": ["Chamber fees", "Professional body membership"],
                "retrieval_aliases": [
                    "Berufsverband",
                    "Mitgliedsbeitrag",
                ],
            },
            "Umzugskosten": {
                "label": "Moving expenses",
                "description": "Moving costs (if work-related)",
                "examples": [
                    "Moving company",
                    "Transport",
                    "Temporary storage",
                    "Travel for apartment search",
                    "Double rent (transitional)",
                ],
                "retrieval_aliases": [
                    "beruflich veranlasster Umzug",
                    "relocation",
                ],
            },
        },
    },
    "Sonderausgaben": {
        "label": "Special expenses",
        "description": "Special personal expenses",
        "subcategories": {
            "Vorsorgeaufwendungen": {
                "label": "Insurance and pension contributions",
                "description": "Insurance premiums and pension contributions",
                "examples": [
                    "Health insurance (Krankenversicherung)",
                    "Long-term care insurance (Pflegeversicherung)",
                    "Pension contributions (Rentenversicherung)",
                    "Riester-Rente",
                    "Rürup-Rente / Basisrente",
                    "Unemployment insurance (Arbeitslosenversicherung)",
                    "Disability insurance (Berufsunfähigkeitsversicherung)",
                    "Liability insurance (Haftpflichtversicherung)",
                    "Accident insurance (Unfallversicherung)",
                    "Life insurance (Risikolebensversicherung)",
                ],
                "retrieval_aliases": [
                    "Versicherungsbeitrag",
                    "Altersvorsorge",
                ],
            },
            "Kirchensteuer": {
                "label": "Church tax",
                "description": "Church tax",
                "examples": ["Church tax paid"],
            },
            "Spenden und Mitgliedsbeiträge": {
                "label": "Donations and membership fees",
                "description": (
                    "Donations and membership fees to charitable organizations"
                ),
                "examples": [
                    "Charitable donations (Zuwendungsbestätigung needed)",
                    "Political party donations",
                    "Donations to universities/research",
                ],
                "retrieval_aliases": [
                    "Spende",
                    "gemeinnützige Organisation",
                ],
            },
            "Ausbildungskosten": {
                "label": "Initial education expenses",
                "description": (
                    "First vocational training or first university degree outside an "
                    "employment relationship"
                ),
                "examples": [
                    "First university degree tuition",
                    "First vocational training costs",
                    "Study materials for first degree",
                    "Learning materials for initial education",
                ],
            },
            "Schulgeld": {
                "label": "School tuition",
                "description": "Private school fees",
                "examples": ["Private school tuition"],
            },
            "Unterhaltsleistungen": {
                "label": "Maintenance payments",
                "description": "Alimony to ex-spouse (Anlage U)",
                "examples": ["Alimony payments"],
            },
        },
    },
    "Außergewöhnliche Belastungen": {
        "label": "Extraordinary expenses",
        "description": (
            "Extraordinary personal burdens (above the zumutbare Belastung threshold)"
        ),
        "subcategories": {
            "Krankheitskosten": {
                "label": "Medical expenses",
                "description": "Medical expenses not covered by insurance",
                "examples": [
                    "Doctor co-pays",
                    "Prescription co-pays",
                    "Dental work / dentures",
                    "Glasses / contact lenses",
                    "Physiotherapy",
                    "Psychological therapy co-pays",
                    "Medical devices",
                    "Hearing aids",
                    "Alternative medicine (with doctor's referral)",
                    "Hospital co-pays",
                    "Travel to medical appointments",
                ],
                "retrieval_aliases": [
                    "Arztrechnung",
                    "Medikamente",
                    "Brille",
                ],
            },
            "Behinderung / Pflege": {
                "label": "Disability and care",
                "description": "Disability and care costs",
                "examples": [
                    "Behinderten-Pauschbetrag",
                    "Pflege-Pauschbetrag",
                    "Care home costs",
                    "Home modifications for disability",
                    "Special transportation needs",
                ],
            },
            "Bestattungskosten": {
                "label": "Funeral expenses",
                "description": "Funeral costs (if estate doesn't cover them)",
                "examples": ["Funeral expenses", "Gravestone", "Cemetery fees"],
            },
            "Wiederbeschaffung nach Katastrophen": {
                "label": "Replacement after disasters",
                "description": "Replacement costs after natural disasters",
                "examples": [
                    "Flood damage",
                    "Fire damage",
                    "Storm damage replacements",
                ],
            },
            "Scheidungskosten": {
                "label": "Divorce expenses",
                "description": "Divorce costs",
                "examples": ["Court fees for divorce proceedings"],
            },
        },
    },
    "Haushaltsnahe Dienstleistungen": {
        "label": "Household services",
        "description": "Household-related services (tax credit on labor costs)",
        "subcategories": {
            "Haushaltsnahe Beschäftigungsverhältnisse": {
                "label": "Household employment",
                "description": (
                    "Household employment (Minijob or employment subject to social "
                    "insurance)"
                ),
                "examples": [
                    "Cleaning service",
                    "Gardener",
                    "Au pair",
                    "Elderly care at home",
                    "Babysitter (employed)",
                ],
            },
            "Haushaltsnahe Dienstleistungen (extern)": {
                "label": "External household services",
                "description": "External household services (labor costs)",
                "examples": [
                    "Professional cleaning company",
                    "Garden maintenance service",
                    "Winter road clearing (Winterdienst)",
                    "Chimney sweep (Schornsteinfeger)",
                    "Pest control",
                    "Window cleaning",
                    "Pet care / dog walking (at your home)",
                    "Laundry / ironing service",
                    "Nebenkostenabrechnung — household-related portions",
                ],
                "retrieval_aliases": [
                    "Fensterreinigung",
                    "Hausmeister",
                    "Gartenpflege",
                ],
            },
            "Handwerkerleistungen": {
                "label": "Tradespeople services",
                "description": "Craftsman services in your home (labor costs)",
                "examples": [
                    "Plumber",
                    "Electrician",
                    "Painter/decorator",
                    "Heating technician",
                    "Locksmith",
                    "Kitchen/bathroom renovation (labor only)",
                    "Floor laying",
                    "Wallpapering",
                    "Appliance repair/installation",
                    "IT setup / computer repair at home",
                    "Garden landscaping (new creation excluded)",
                ],
                "retrieval_aliases": [
                    "Reparatur",
                    "Wartung",
                    "Arbeitskosten",
                ],
            },
        },
    },
    "Vermietung und Verpachtung": {
        "label": "Rental income expenses",
        "description": "Expenses related to rental properties (Anlage V)",
        "subcategories": {
            "Abschreibung (AfA)": {
                "label": "Depreciation",
                "description": "Depreciation of rental property",
                "examples": ["Building depreciation"],
            },
            "Erhaltungsaufwand": {
                "label": "Property maintenance",
                "description": "Maintenance and repair of rental property",
                "examples": ["Repairs", "Renovation", "New heating system"],
                "retrieval_aliases": [
                    "Instandhaltung",
                    "Renovierung",
                    "vermietete Immobilie",
                ],
            },
            "Finanzierungskosten": {
                "label": "Financing costs",
                "description": "Financing costs",
                "examples": ["Mortgage interest", "Loan processing fees"],
                "retrieval_aliases": [
                    "Darlehenszinsen",
                    "Finanzierung",
                ],
            },
            "Nebenkosten (Vermieter)": {
                "label": "Landlord operating expenses",
                "description": "Landlord's ancillary costs",
                "examples": [
                    "Property tax (Grundsteuer)",
                    "Building insurance",
                    "Property management",
                    "Legal fees",
                ],
            },
        },
    },
    "Kapitalerträge": {
        "label": "Investment income",
        "description": "Investment income",
        "subcategories": {
            "Sparerpauschbetrag": {
                "label": "Savings allowance",
                "description": "Saver's flat-rate allowance",
                "examples": ["Interest income", "Dividend income", "Capital gains"],
            },
            "Freistellungsauftrag": {
                "label": "Tax exemption order",
                "description": "Exemption order filed with bank",
                "examples": ["Bank exemption order"],
            },
        },
    },
    "Nicht abzugsfähig": {
        "label": "Not deductible",
        "description": "NOT tax deductible (common items)",
        "subcategories": {
            "Lebenshaltungskosten": {
                "label": "Personal living expenses",
                "description": "General living expenses",
                "examples": [
                    "Groceries",
                    "Regular clothing",
                    "Entertainment",
                    "Restaurants (private)",
                    "Vacations",
                    "Hobbies",
                    "Gym membership (usually)",
                    "Streaming subscriptions",
                    "Personal electronics (non-work)",
                    "Furniture (non-work)",
                ],
                "retrieval_aliases": [
                    "Lebensmittel",
                    "Privatkleidung",
                    "Freizeit",
                    "private Haushaltsgeräte",
                ],
            },
        },
    },
}


# English UI labels; persisted tax categories keep their canonical German keys.
_LABELS = {
    "Unklassifiziert": "Unclassified",
    **{name: details["label"] for name, details in TAX_CATEGORIES.items()},
    **{
        name: details["label"]
        for category in TAX_CATEGORIES.values()
        for name, details in category["subcategories"].items()
    },
}


def english_label(value):
    return _LABELS.get(value, value)


def canonical_categories(category, subcategory):
    """Accept English labels or existing German keys, within the chosen category."""
    category = next((key for key in TAX_CATEGORIES
                     if category.casefold() in {key.casefold(), english_label(key).casefold()}), category)
    if category in TAX_CATEGORIES:
        subcategory = next((key for key in TAX_CATEGORIES[category]["subcategories"]
                            if subcategory.casefold() in {key.casefold(), english_label(key).casefold()}), subcategory)
    return category, subcategory


def can_be_deductible(category, subcategory) -> bool:
    """Whether a reviewer may confirm this category pair as deductible."""
    return (
        category != "Nicht abzugsfähig"
        and category in TAX_CATEGORIES
        and subcategory in TAX_CATEGORIES[category]["subcategories"]
    )


def get_tax_info_message() -> str:
    lines = [
        "<b>German Tax Deduction Categories</b>",
        "<i>Classification depends on the expense and applicable tax rules.</i>\n",
    ]

    for cat_name, cat_data in TAX_CATEGORIES.items():
        # German category names are the terms used on tax forms.
        lines.append(f"<b>{cat_data['label']}</b> <i>({cat_name})</i>")
        lines.append(f"<i>{cat_data['description']}</i>")

        for sub_data in cat_data["subcategories"].values():
            lines.append(f"  ├ <b>{sub_data['label']}</b>")
            lines.append(f"  │ {sub_data['description']}")

        lines.append("")

    lines.append("<i>Use /summary to see your deductible expenses by category.</i>")
    return "\n".join(lines)
