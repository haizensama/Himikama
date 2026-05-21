"""
himikama/backend/ingestion/constitutional_articles.py
═══════════════════════════════════════════════════════════════
Sri Lankan Constitution — Chapter III
Fundamental Rights (Articles 10–17)

Source: Constitution of the Democratic Socialist Republic
        of Sri Lanka (1978), incorporating amendments up to
        the 21st Amendment (2022).

⚠ VERIFICATION REQUIRED BEFORE INGESTION ⚠
    Cross-check every article against the official text at:
    https://www.parliament.lk/constitution-of-sri-lanka
    Pay particular attention to:
        - Article 14(A) — Right to Information (19th Amendment)
        - Article 15   — Restrictions on Fundamental Rights
        - Any sub-articles your case corpus cites that are
          not listed here

Structure:
    Each article is a dict with:
        article_number: canonical citation string
        chapter:        "3"
        heading:        short descriptive title
        text:           verbatim constitutional text

    The ChromaDB document = heading + ". " + text
    This is what gets embedded for semantic search.

Usage:
    from ingestion.constitutional_articles import CHAPTER_3_ARTICLES
    from ingestion.embedder import embed_articles

    embed_articles(CHAPTER_3_ARTICLES, db_path="db/")
═══════════════════════════════════════════════════════════════
"""

CHAPTER_3_ARTICLES = [

    # ─────────────────────────────────────────────────────────
    # ARTICLE 10 — Freedom of thought, conscience and religion
    # ─────────────────────────────────────────────────────────
    {
        "article_number": "10",
        "chapter": "3",
        "heading": "Freedom of thought, conscience and religion",
        "text": (
            "Every person is entitled to freedom of thought, "
            "conscience and religion, including the freedom to "
            "have or to adopt a religion or belief of his choice."
        ),
    },

    # ─────────────────────────────────────────────────────────
    # ARTICLE 11 — Freedom from torture
    # ─────────────────────────────────────────────────────────
    {
        "article_number": "11",
        "chapter": "3",
        "heading": (
            "Freedom from torture, cruel, inhuman or "
            "degrading treatment or punishment"
        ),
        "text": (
            "No person shall be subjected to torture or to cruel, "
            "inhuman or degrading treatment or punishment."
        ),
    },

    # ─────────────────────────────────────────────────────────
    # ARTICLE 12 — Right to equality
    # ─────────────────────────────────────────────────────────
    {
        "article_number": "12(1)",
        "chapter": "3",
        "heading": "Right to equality before the law",
        "text": (
            "All persons are equal before the law and are entitled "
            "to the equal protection of the law."
        ),
    },
    {
        "article_number": "12(2)",
        "chapter": "3",
        "heading": "Freedom from discrimination",
        "text": (
            "No citizen shall be discriminated against on the "
            "grounds of race, religion, language, caste, sex, "
            "political opinion, place of birth or any one of such "
            "grounds. Provided that it shall be lawful to require "
            "a person to acquire within a reasonable time sufficient "
            "knowledge of any language as a qualification for any "
            "employment or office in the Public, Judicial or Local "
            "Government Service or in the service of any Public "
            "Corporation, where such knowledge is reasonably necessary "
            "for the discharge of the duties of such employment or office. "
            "Provided further that it shall be lawful to require "
            "a person to have a sufficient knowledge of any language "
            "as a qualification for any such employment or office where no "
            "function of that employment or office can be discharged "
            "otherwise than with a knowledge of that language."
        ),
    },
    {
        "article_number": "12(3)",
        "chapter": "3",
        "heading": "Freedom of access to public places and worship",
        "text": (
            "No person shall, on the grounds of race, religion, "
            "language, caste, sex or any one of such grounds, be "
            "subject to any disability, liability, restriction or condition "
            "with regard to access to shops, public restaurants, hotels, "
            "places of public entertainment and places of public "
            "worship of his own religion."
        ),
    },
    {
        "article_number": "12(4)",
        "chapter": "3",
        "heading": "Affirmative action for advancement",
        "text": (
            "Nothing in this Article shall prevent special "
            "provision being made, by law, subordinate legislation "
            "or executive action, for the advancement of women, "
            "children or disabled persons."
        ),
    },

    # ─────────────────────────────────────────────────────────
    # ARTICLE 13 — Freedom from arbitrary arrest and detention
    # ─────────────────────────────────────────────────────────
    {
        "article_number": "13(1)",
        "chapter": "3",
        "heading": "Freedom from arbitrary arrest",
        "text": (
            "No person shall be arrested except according to "
            "procedure established by law. Any person arrested shall "
            "be informed of the reason for his arrest."
        ),
    },
    {
        "article_number": "13(2)",
        "chapter": "3",
        "heading": "Right to be produced before a judge",
        "text": (
            "Every person held in custody, detained or otherwise "
            "deprived of personal liberty shall be brought before "
            "the judge of the nearest competent court according to "
            "procedure established by law and shall not be further "
            "held in custody, detained or deprived of personal "
            "liberty except upon and in terms of the order of such "
            "judge made in accordance with procedure established "
            "by law."
        ),
    },
    {
        "article_number": "13(3)",
        "chapter": "3",
        "heading": "Right to a fair trial",
        "text": (
            "Any person charged with an offence shall be entitled "
            "to be heard, in person or by an Attorney-at-Law, at "
            "a fair trial by a competent court."
        ),
    },
    {
        "article_number": "13(4)",
        "chapter": "3",
        "heading": "Punishment only by order of a competent court",
        "text": (
            "No person shall be punished with death or imprisonment "
            "except by order of a competent court, made in "
            "accordance with procedure established by law. The "
            "arrest, holding in custody, detention or other "
            "deprivation of personal liberty of a person, pending "
            "investigation or trial, shall not constitute punishment."
        ),
    },
    {
        "article_number": "13(5)",
        "chapter": "3",
        "heading": "Presumption of innocence",
        "text": (
            "Every person shall be presumed innocent until he is "
            "proved guilty. Provided that the burden of proving "
            "particular facts may, by law, be placed on an accused "
            "person."
        ),
    },
    {
        "article_number": "13(6)",
        "chapter": "3",
        "heading": "Freedom from retrospective penal legislation",
        "text": (
            "No person shall be held guilty of an offence on "
            "account of any act or omission which did not, at the "
            "time of such act or omission, constitute such an offence "
            "and no penalty shall be imposed for any offence more "
            "severe than the penalty in force at the time such offence "
            "was committed. Nothing in this Article shall prejudice "
            "the trial and punishment of any person for any act or "
            "omission which, at the time when it was committed, was "
            "criminal according to the general principles of law "
            "recognized by the community of nations. It shall not be "
            "contravention of this Article to require the imposition "
            "of a minimum penalty for an offence provided that such "
            "penalty does not exceed the maximum penalty prescribed "
            "for such offence at the time such offence was committed."
        ),
    },
    {
        "article_number": "13(7)",
        "chapter": "3",
        "heading": "Removal or deportation orders",
        "text": (
            "The arrest, holding in custody, detention or other "
            "deprivation of personal liberty of a person, by reason "
            "of a removal order or a deportation order made under "
            "the provisions of the Immigrants and Emigrants Act "
            "or the Indo-Ceylon Agreement (Implementation) Act, "
            "No. 14 of 1967, or such other law as may be enacted "
            "in substitution therefor, shall not be a contravention "
            "of this Article."
        ),
    },

    # ─────────────────────────────────────────────────────────
    # ARTICLE 14 — Freedom of speech, assembly, association,
    #              occupation and movement
    # ─────────────────────────────────────────────────────────
    {
        "article_number": "14(1)(a)",
        "chapter": "3",
        "heading": "Freedom of speech and expression",
        "text": (
            "Every citizen is entitled to the freedom of speech "
            "and expression including publication."
        ),
    },
    {
        "article_number": "14(1)(b)",
        "chapter": "3",
        "heading": "Freedom of peaceful assembly",
        "text": (
            "Every citizen is entitled to the freedom of peaceful "
            "assembly."
        ),
    },
    {
        "article_number": "14(1)(c)",
        "chapter": "3",
        "heading": "Freedom of association",
        "text": (
            "Every citizen is entitled to the freedom of "
            "association."
        ),
    },
    {
        "article_number": "14(1)(d)",
        "chapter": "3",
        "heading": "Freedom to form and join trade unions",
        "text": (
            "Every citizen is entitled to the freedom to form and "
            "join a trade union."
        ),
    },
    {
        "article_number": "14(1)(e)",
        "chapter": "3",
        "heading": "Freedom to manifest religion or belief",
        "text": (
            "Every citizen is entitled to the freedom, either by "
            "himself or in association with others, and either in "
            "public or in private, to manifest his religion or "
            "belief in worship, observance, practice and teaching."
        ),
    },
    {
        "article_number": "14(1)(f)",
        "chapter": "3",
        "heading": "Freedom to enjoy and promote culture and language",
        "text": (
            "Every citizen is entitled to the freedom by himself "
            "or in association with others to enjoy and promote "
            "his own culture and to use his own language."
        ),
    },
    {
        "article_number": "14(1)(g)",
        "chapter": "3",
        "heading": "Freedom to engage in lawful occupation",
        "text": (
            "Every citizen is entitled to the freedom to engage "
            "by himself or in association with others in any "
            "lawful occupation, profession, trade, business or "
            "enterprise."
        ),
    },
    {
        "article_number": "14(1)(h)",
        "chapter": "3",
        "heading": "Freedom of movement and choice of residence",
        "text": (
            "Every citizen is entitled to the freedom of movement "
            "and of choosing his residence within Sri Lanka."
        ),
    },
    {
        "article_number": "14(1)(i)",
        "chapter": "3",
        "heading": "Freedom to return to Sri Lanka",
        "text": (
            "Every citizen is entitled to the freedom to return "
            "to Sri Lanka."
        ),
    },
    {
        "article_number": "14(2)",
        "chapter": "3",
        "heading": "Rights of certain permanent residents",
        "text": (
            "A person who, not being a citizen of any other country, "
            "has been permanently and legally resident in Sri Lanka "
            "immediately prior to the commencement of the Constitution "
            "and continues to be so resident shall be entitled, for a "
            "period of ten years from the commencement of the "
            "Constitution, to the rights declared and recognized by "
            "paragraph (1) of this Article."
        ),
    },

    # ─────────────────────────────────────────────────────────
    # ARTICLE 14A — Right of access to information
    # (Inserted by the 19th Amendment to the Constitution, 2015)
    # ─────────────────────────────────────────────────────────
    {
        "article_number": "14(A)",
        "chapter": "3",
        "heading": "Right of access to information",
        "text": (
            "Every citizen shall have the right of access to any "
            "information as provided for by law, being information "
            "that is required for the exercise or protection of a "
            "citizen's right held by: "
            "(a) the State, a Ministry or any Government Department "
            "or any statutory body established or created by or under "
            "any law; "
            "(b) any Ministry of a Minister of the Board of Ministers "
            "of a Province or any Department or any statutory body "
            "established or created by a statute of a Provincial Council; "
            "(c) any local authority; and "
            "(d) any other person, who is in possession of such "
            "information relating to any institution referred to in "
            "sub-paragraphs (a), (b) or (c) of this paragraph."
        ),
    },
    {
        "article_number": "14(A)(2)",
        "chapter": "3",
        "heading": "Restrictions on right of access to information",
        "text": (
            "No restrictions shall be placed on the right declared "
            "and recognized by this Article, other than such restrictions "
            "prescribed by law as are necessary in a democratic society, "
            "in the interests of national security, territorial integrity "
            "or public safety, for the prevention of disorder or crime, "
            "for the protection of health or morals and of the reputation "
            "or the rights of others, privacy, prevention of contempt of "
            "court, protection of parliamentary privilege, for preventing "
            "the disclosure of information communicated in confidence, "
            "or for maintaining the authority and impartiality of the "
            "judiciary."
        ),
    },
    {
        "article_number": "14A(3)",
        "chapter": "3",
        "heading": "Meaning of citizen for right of access to information",
        "text": (
            "In this Article, citizen includes a body whether "
            "incorporated or unincorporated, if not less than "
            "three-fourths of the members of such body are citizens."
        ),
    },

    # ─────────────────────────────────────────────────────────
    # ARTICLE 15 — Restrictions on fundamental rights
    # ─────────────────────────────────────────────────────────
    {
        "article_number": "15(1)",
        "chapter": "3",
        "heading": "Restrictions on presumption of innocence and retrospective penalties",
        "text": (
            "The exercise and operation of the fundamental rights "
            "declared and recognized by Articles 13(5) and 13(6) "
            "shall be subject only to such restrictions as may be "
            "prescribed by law in the interests of national security. "
            "For the purposes of this paragraph law includes regulations "
            "made under the law for the time being relating to public "
            "security."
        ),
    },
    {
        "article_number": "15(2)",
        "chapter": "3",
        "heading": "Restrictions on freedom of speech and expression",
        "text": (
            "The exercise and operation of the fundamental right "
            "declared and recognized by Article 14(1)(a) shall be "
            "subject to such restrictions as may be prescribed by "
            "law in the interests of racial and religious harmony or "
            "in relation to parliamentary privilege, contempt of court, "
            "defamation or incitement to an offence."
        ),
    },
    {
        "article_number": "15(3)",
        "chapter": "3",
        "heading": "Restrictions on freedom of peaceful assembly",
        "text": (
            "The exercise and operation of the fundamental right "
            "declared and recognized by Article 14(1)(b) shall be "
            "subject to such restrictions as may be prescribed by "
            "law in the interests of racial and religious harmony."
        ),
    },
    {
        "article_number": "15(4)",
        "chapter": "3",
        "heading": "Restrictions on freedom of association",
        "text": (
            "The exercise and operation of the fundamental right "
            "declared and recognized by Article 14(1)(c) shall be "
            "subject to such restrictions as may be prescribed by "
            "law in the interests of racial and religious harmony or "
            "national economy."
        ),
    },
    {
        "article_number": "15(5)",
        "chapter": "3",
        "heading": "Restrictions on freedom of occupation",
        "text": (
            "The exercise and operation of the fundamental right "
            "declared and recognized by Article 14(1)(g) shall be "
            "subject to such restrictions as may be prescribed by "
            "law in the interests of national economy or in relation to: "
            "(a) the professional, technical, academic, financial and "
            "other qualifications necessary for practising any profession "
            "or carrying on any occupation, trade, business or enterprise "
            "and the licensing and disciplinary control of the person "
            "entitled to such fundamental right; and "
            "(b) the carrying on by the State, a State agency or a "
            "public corporation of any trade, business, industry, service "
            "or enterprise whether to the exclusion, complete or partial, "
            "of citizens or otherwise."
        ),
    },
    {
        "article_number": "15(6)",
        "chapter": "3",
        "heading": "Restrictions on freedom of movement and residence",
        "text": (
            "The exercise and operation of the fundamental right "
            "declared and recognized by Article 14(1)(h) shall be "
            "subject to such restrictions as may be prescribed by "
            "law in the interests of national economy."
        ),
    },
    {
        "article_number": "15(7)",
        "chapter": "3",
        "heading": (
            "General restrictions on fundamental rights in interests "
            "of national security, public order, health, morality and welfare"
        ),
        "text": (
            "The exercise and operation of all the fundamental rights "
            "declared and recognized by Articles 12, 13(1), 13(2) "
            "and 14 shall be subject to such restrictions as may be "
            "prescribed by law in the interests of national security, "
            "public order and the protection of public health or morality, "
            "or for the purpose of securing due recognition and respect "
            "for the rights and freedoms of others, or of meeting the just "
            "requirements of the general welfare of a democratic society. "
            "For the purposes of this paragraph law includes regulations "
            "made under the law for the time being relating to public "
            "security."
        ),
    },
    {
        "article_number": "15(8)",
        "chapter": "3",
        "heading": "Restrictions applicable to Armed Forces, Police Force and other Forces",
        "text": (
            "The exercise and operation of the fundamental rights "
            "declared and recognized by Articles 12(1), 13 and 14 "
            "shall, in their application to the members of the Armed "
            "Forces, Police Force and other Forces charged with the "
            "maintenance of public order, be subject to such restrictions "
            "as may be prescribed by law in the interests of the proper "
            "discharge of their duties and the maintenance of discipline "
            "among them."
        ),
    },

    # ─────────────────────────────────────────────────────────
    # ARTICLE 16 — Existing written law and unwritten law
    # ─────────────────────────────────────────────────────────
    {
        "article_number": "16(1)",
        "chapter": "3",
        "heading": "Existing written law and unwritten law",
        "text": (
            "All existing written law and unwritten law shall be "
            "valid and operative notwithstanding any inconsistency "
            "with the preceding provisions of this Chapter."
        ),
    },
    {
        "article_number": "16(2)",
        "chapter": "3",
        "heading": "Punishment under existing written law",
        "text": (
            "The subjection of any person on the order of a competent "
            "court to any form of punishment recognized by any existing "
            "written law shall not be a contravention of the provisions "
            "of this Chapter."
        ),
    },

    # ─────────────────────────────────────────────────────────
    # ARTICLE 17 — Remedy for infringement of fundamental rights
    # ─────────────────────────────────────────────────────────
    {
        "article_number": "17",
        "chapter": "3",
        "heading": "Remedy for infringement of fundamental rights",
        "text": (
            "Every person shall be entitled to apply to the Supreme "
            "Court, as provided by Article 126, in respect of the "
            "infringement or imminent infringement, by executive or "
            "administrative action, of a fundamental right to which "
            "such person is entitled under the provisions of this Chapter."
        ),
    },

    # ─────────────────────────────────────────────────────────
    # ARTICLE 126 — Jurisdiction of the Supreme Court
    # (Referenced in FR cases — included for retrieval context)
    # ─────────────────────────────────────────────────────────
    {
        "article_number": "126",
        "chapter": "3",
        "heading": (
            "Jurisdiction of Supreme Court in respect of "
            "fundamental rights"
        ),
        "text": (
            "The Supreme Court shall have sole and exclusive "
            "jurisdiction to hear and determine any question "
            "relating to the infringement or imminent infringement "
            "by executive or administrative action of any "
            "fundamental right or language right declared and "
            "recognised by Chapter III or Chapter IV. Where any "
            "person alleges that any such fundamental right or "
            "language right relating to such person has been "
            "infringed or is about to be infringed by executive "
            "or administrative action, he may himself or by an "
            "attorney-at-law on his behalf, within one month "
            "thereof, in accordance with such rules of court as "
            "may be in force, apply to the Supreme Court by way "
            "of petition in writing addressed to such Court "
            "praying for relief or redress in respect of such "
            "infringement. Such application shall be heard by "
            "three Judges of the Supreme Court and may be "
            "supported by affidavit."
        ),
    },

    # ─────────────────────────────────────────────────────────
    # ARTICLE 4(d) — Referenced in some FR cases
    # ─────────────────────────────────────────────────────────
    {
        "article_number": "4(d)",
        "chapter": "3",
        "heading": "Guarantee of fundamental rights",
        "text": (
            "The fundamental rights which are by the Constitution "
            "declared and recognised shall be respected, secured "
            "and advanced by all the organs of government, and "
            "shall not be abridged, restricted or denied, save in "
            "the manner and to the extent hereinafter provided."
        ),
    },
]


# ─────────────────────────────────────────────────────────────
# QUICK INSPECTION UTILITY
# ─────────────────────────────────────────────────────────────

def list_articles() -> None:
    """Print all articles in this file for quick verification."""
    print(f"\nChapter 3 Articles — {len(CHAPTER_3_ARTICLES)} total")
    print("=" * 55)
    for article in CHAPTER_3_ARTICLES:
        num     = article["article_number"]
        heading = article["heading"]
        words   = len(article["text"].split())
        print(f"  Article {num:<10} ({words:>3} words) — {heading}")
    print("=" * 55)


if __name__ == "__main__":
    list_articles()