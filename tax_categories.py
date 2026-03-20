from __future__ import annotations
"""
German Tax Deduction Categories — Steuerklasse 1, unmarried, separate declarations.

Comprehensive list of ALL possible deduction categories for the German
Einkommensteuererklärung (income tax declaration).
"""

TAX_CATEGORIES = {
    # ══════════════════════════════════════════════════════════════════════════
    # 1. WERBUNGSKOSTEN (Income-related expenses)
    #    Deducted from employment income. Pauschbetrag: €1,230/year
    # ══════════════════════════════════════════════════════════════════════════
    "Werbungskosten": {
        "description": "Income-related expenses (employment)",
        "pauschbetrag": 1230,
        "subcategories": {
            "Fahrtkosten / Entfernungspauschale": {
                "description": "Commuting costs: €0.30/km (first 20km), €0.38/km (from 21st km) one way",
                "examples": ["Public transport tickets", "Car fuel for commute", "Bicycle for commute"],
            },
            "Arbeitsmittel": {
                "description": "Work equipment and tools",
                "examples": [
                    "Laptop / Computer", "Monitor / peripherals",
                    "Software licenses", "Office supplies",
                    "Professional books", "Tools for work",
                    "Work bag / briefcase", "Work clothing (if specific)",
                ],
            },
            "Homeoffice-Pauschale": {
                "description": "Home office flat rate: €6/day, max €1,260/year (210 days)",
                "examples": ["Working from home days"],
            },
            "Arbeitszimmer": {
                "description": "Dedicated home office room (if center of professional activity)",
                "examples": ["Rent proportion", "Utilities proportion", "Office furniture"],
            },
            "Fortbildungskosten": {
                "description": "Professional development and training",
                "examples": [
                    "Course fees", "Seminars", "Conferences",
                    "Professional certifications", "Study materials",
                    "Exam fees", "Travel to training",
                ],
            },
            "Bewerbungskosten": {
                "description": "Job application costs",
                "examples": [
                    "Application photos", "Copies", "Postage",
                    "Travel to interviews", "Professional CV service",
                ],
            },
            "Doppelte Haushaltsführung": {
                "description": "Double household costs (if work requires second home)",
                "examples": [
                    "Second apartment rent (max €1,000/month)",
                    "Moving costs", "Weekly trips home",
                    "Household items for second home",
                ],
            },
            "Reisekosten (Dienstreisen)": {
                "description": "Business travel expenses",
                "examples": [
                    "Train/flight tickets", "Hotel", "Rental car",
                    "Meals (Verpflegungsmehraufwand)",
                    "Parking fees during business travel",
                ],
            },
            "Kontoführungsgebühren": {
                "description": "Bank account fees (Pauschale: €16/year)",
                "examples": ["Bank fees for salary account"],
            },
            "Telefon / Internet": {
                "description": "Phone and internet for work use (typically 20% deductible or actual proof)",
                "examples": ["Mobile phone bill", "Internet bill", "Work phone"],
            },
            "Gewerkschaftsbeiträge": {
                "description": "Trade union membership fees",
                "examples": ["Union dues", "ver.di", "IG Metall"],
            },
            "Berufsverbände": {
                "description": "Professional association fees",
                "examples": ["Chamber fees", "Professional body membership"],
            },
            "Umzugskosten": {
                "description": "Moving costs (if work-related)",
                "examples": [
                    "Moving company", "Transport", "Temporary storage",
                    "Travel for apartment search", "Double rent (transitional)",
                ],
            },
        },
    },

    # ══════════════════════════════════════════════════════════════════════════
    # 2. SONDERAUSGABEN (Special expenses)
    # ══════════════════════════════════════════════════════════════════════════
    "Sonderausgaben": {
        "description": "Special personal expenses",
        "subcategories": {
            "Vorsorgeaufwendungen": {
                "description": "Insurance premiums and pension contributions",
                "examples": [
                    "Health insurance (Krankenversicherung)",
                    "Long-term care insurance (Pflegeversicherung)",
                    "Pension contributions (Rentenversicherung)",
                    "Riester-Rente", "Rürup-Rente / Basisrente",
                    "Unemployment insurance (Arbeitslosenversicherung)",
                    "Disability insurance (Berufsunfähigkeitsversicherung)",
                    "Liability insurance (Haftpflichtversicherung)",
                    "Accident insurance (Unfallversicherung)",
                    "Life insurance (Risikolebensversicherung)",
                ],
            },
            "Kirchensteuer": {
                "description": "Church tax (fully deductible)",
                "examples": ["Church tax paid"],
            },
            "Spenden und Mitgliedsbeiträge": {
                "description": "Donations and membership fees to charitable organizations",
                "examples": [
                    "Charitable donations (Zuwendungsbestätigung needed)",
                    "Political party donations (max €1,650 direct credit)",
                    "Donations to universities/research",
                ],
            },
            "Ausbildungskosten": {
                "description": "First-degree education costs (max €6,000/year as Sonderausgaben)",
                "examples": [
                    "First university degree tuition",
                    "First vocational training costs",
                    "Study materials for first degree",
                ],
            },
            "Schulgeld": {
                "description": "Private school fees (30%, max €5,000/year per child)",
                "examples": ["Private school tuition"],
            },
            "Unterhaltsleistungen": {
                "description": "Alimony to ex-spouse (max €13,805/year, Anlage U)",
                "examples": ["Alimony payments"],
            },
        },
    },

    # ══════════════════════════════════════════════════════════════════════════
    # 3. AUßERGEWÖHNLICHE BELASTUNGEN (Extraordinary expenses)
    # ══════════════════════════════════════════════════════════════════════════
    "Außergewöhnliche Belastungen": {
        "description": "Extraordinary personal burdens (above zumutbare Belastung threshold)",
        "subcategories": {
            "Krankheitskosten": {
                "description": "Medical expenses not covered by insurance",
                "examples": [
                    "Doctor co-pays", "Prescription co-pays",
                    "Dental work / dentures", "Glasses / contact lenses",
                    "Physiotherapy", "Psychological therapy co-pays",
                    "Medical devices", "Hearing aids",
                    "Alternative medicine (with doctor's referral)",
                    "Hospital co-pays", "Travel to medical appointments",
                ],
            },
            "Behinderung / Pflege": {
                "description": "Disability and care costs",
                "examples": [
                    "Behinderten-Pauschbetrag",
                    "Pflege-Pauschbetrag (€1,800 for Pflegegrad 4-5)",
                    "Care home costs", "Home modifications for disability",
                    "Special transportation needs",
                ],
            },
            "Bestattungskosten": {
                "description": "Funeral costs (if estate doesn't cover them)",
                "examples": ["Funeral expenses", "Gravestone", "Cemetery fees"],
            },
            "Wiederbeschaffung nach Katastrophen": {
                "description": "Replacement costs after natural disasters",
                "examples": ["Flood damage", "Fire damage", "Storm damage replacements"],
            },
            "Scheidungskosten": {
                "description": "Divorce costs (very limited since 2013)",
                "examples": ["Court fees for divorce proceedings"],
            },
        },
    },

    # ══════════════════════════════════════════════════════════════════════════
    # 4. HAUSHALTSNAHE DIENSTLEISTUNGEN & HANDWERKERLEISTUNGEN
    #    (§35a EStG — direct tax CREDIT, not deduction)
    # ══════════════════════════════════════════════════════════════════════════
    "Haushaltsnahe Dienstleistungen": {
        "description": "Household-related services (20% of labor costs, max €4,000 tax credit)",
        "subcategories": {
            "Haushaltsnahe Beschäftigungsverhältnisse": {
                "description": "Household employment (Minijob: max €510 credit; sozialversichert: max €4,000)",
                "examples": [
                    "Cleaning service", "Gardener", "Au pair",
                    "Elderly care at home", "Babysitter (employed)",
                ],
            },
            "Haushaltsnahe Dienstleistungen (extern)": {
                "description": "External household services (20% of labor, max €4,000 credit)",
                "examples": [
                    "Professional cleaning company",
                    "Garden maintenance service",
                    "Winter road clearing (Winterdienst)",
                    "Chimney sweep (Schornsteinfeger)",
                    "Pest control", "Window cleaning",
                    "Pet care / dog walking (at your home)",
                    "Laundry / ironing service",
                    "Nebenkostenabrechnung — household-related portions",
                ],
            },
            "Handwerkerleistungen": {
                "description": "Craftsman services in your home (20% of labor, max €1,200 credit)",
                "examples": [
                    "Plumber", "Electrician", "Painter/decorator",
                    "Heating technician", "Locksmith",
                    "Kitchen/bathroom renovation (labor only)",
                    "Floor laying", "Wallpapering",
                    "Appliance repair/installation",
                    "IT setup / computer repair at home",
                    "Garden landscaping (new creation excluded)",
                ],
            },
        },
    },

    # ══════════════════════════════════════════════════════════════════════════
    # 5. VERMIETUNG UND VERPACHTUNG (Rental income expenses)
    # ══════════════════════════════════════════════════════════════════════════
    "Vermietung und Verpachtung": {
        "description": "Expenses related to rental properties (Anlage V)",
        "subcategories": {
            "Abschreibung (AfA)": {
                "description": "Depreciation of rental property",
                "examples": ["Building depreciation (2% or 2.5% per year)"],
            },
            "Erhaltungsaufwand": {
                "description": "Maintenance and repair of rental property",
                "examples": ["Repairs", "Renovation", "New heating system"],
            },
            "Finanzierungskosten": {
                "description": "Financing costs",
                "examples": ["Mortgage interest", "Loan processing fees"],
            },
            "Nebenkosten (Vermieter)": {
                "description": "Landlord's ancillary costs",
                "examples": [
                    "Property tax (Grundsteuer)", "Building insurance",
                    "Property management", "Legal fees",
                ],
            },
        },
    },

    # ══════════════════════════════════════════════════════════════════════════
    # 6. KAPITALERTRÄGE (Investment-related)
    # ══════════════════════════════════════════════════════════════════════════
    "Kapitalerträge": {
        "description": "Investment income (Sparerpauschbetrag: €1,000/person)",
        "subcategories": {
            "Sparerpauschbetrag": {
                "description": "Saver's flat-rate allowance €1,000",
                "examples": ["Interest income", "Dividend income", "Capital gains"],
            },
            "Freistellungsauftrag": {
                "description": "Exemption order filed with bank",
                "examples": ["Bank exemption order"],
            },
        },
    },

    # ══════════════════════════════════════════════════════════════════════════
    # 7. NICHT ABZUGSFÄHIG (Not deductible — common misconceptions)
    # ══════════════════════════════════════════════════════════════════════════
    "Nicht abzugsfähig": {
        "description": "NOT tax deductible (common items)",
        "subcategories": {
            "Lebenshaltungskosten": {
                "description": "General living expenses",
                "examples": [
                    "Groceries", "Regular clothing", "Entertainment",
                    "Restaurants (private)", "Vacations", "Hobbies",
                    "Gym membership (usually)", "Streaming subscriptions",
                    "Personal electronics (non-work)", "Furniture (non-work)",
                ],
            },
        },
    },
}


def get_tax_info_message() -> str:
    """Generate a formatted Telegram message with all tax categories."""
    lines = [
        "<b> German Tax Deduction Categories</b>",
        "<i>Steuerklasse 1 · Unmarried · Separate Steuererklärung</i>\n",
    ]

    emoji_map = {
        "Werbungskosten": "",
        "Sonderausgaben": "",
        "Außergewöhnliche Belastungen": "",
        "Haushaltsnahe Dienstleistungen": "",
        "Vermietung und Verpachtung": "",
        "Kapitalerträge": "",
        "Nicht abzugsfähig": "",
    }

    for cat_name, cat_data in TAX_CATEGORIES.items():
        emoji = emoji_map.get(cat_name, "")
        lines.append(f"{emoji} <b>{cat_name}</b>")
        lines.append(f"<i>{cat_data['description']}</i>")
        if "pauschbetrag" in cat_data:
            lines.append(f"  Pauschbetrag: €{cat_data['pauschbetrag']:,}")

        for sub_name, sub_data in cat_data["subcategories"].items():
            lines.append(f"  ├ <b>{sub_name}</b>")
            lines.append(f"  │ {sub_data['description']}")

        lines.append("")

    lines.append(
        " <i>Use /summary to see your deductible expenses by category.</i>"
    )
    return "\n".join(lines)
