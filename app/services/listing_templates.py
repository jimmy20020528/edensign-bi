"""The 5 user-selectable listing writing templates (anonymized; static definitions).
Single source of truth for prompts + the UI definition/backing copy."""

# Shared backbone appended to every template's system prompt.
_BACKBONE = (
    "You are a real estate copywriter. Rules for every style: lead with the single "
    "strongest selling point; be specific — name real materials, finishes, and "
    "measurements from the data, never generic words like 'beautiful'; stay accurate "
    "(never call a 900 sqft home 'sprawling'); keep it scannable. Return valid JSON only."
)

_TEMPLATES = {
    "concise": {
        "id": "concise",
        "label": "Concise",
        "definition": "Leads with the single strongest feature; short and skimmable.",
        "backed_by": "Listing-platform data: the first ~50 words decide on mobile/search; tight, specific copy gets more showings.",
        "system_prompt": _BACKBONE + " Style: extremely concise and confident. No hedging, no filler.",
        "paragraph_instruction": "1-2 short paragraphs, 60-125 words total. Front-load the best feature in the first sentence.",
    },
    "word_optimized": {
        "id": "word_optimized",
        "label": "Word-Optimized",
        "definition": "Uses words shown to raise sale price; avoids words that lower it.",
        "backed_by": "Zillow analysis of listing language vs. sale outcome ('luxurious'/'captivating' correlate with above-expected prices).",
        "system_prompt": _BACKBONE + " Style: factual and precise. Favor high-performing concrete words; avoid 'fixer', 'TLC', 'cosmetic', 'investor', 'potential', 'bargain', 'nice'.",
        "paragraph_instruction": "3-4 paragraphs, ~250 words. Headline + overview + scannable feature highlights.",
    },
    "aida": {
        "id": "aida",
        "label": "AIDA",
        "definition": "Attention to Interest to Desire to Action; leads with feeling, follows with facts.",
        "backed_by": "The AIDA model, a classic advertising framework rooted in consumer psychology.",
        "system_prompt": _BACKBONE + " Style: follow AIDA. Open with an attention hook, build interest and desire with sensory specifics, end with a soft call to action.",
        "paragraph_instruction": "4-5 paragraphs, ~200-250 words. P1 hook, P2-3 sensory desire, last line a soft CTA.",
    },
    "story": {
        "id": "story",
        "label": "Story",
        "definition": "Tells the home's story with the buyer as the protagonist.",
        "backed_by": "Stories are ~22x more memorable than facts (Bruner); narrative copy generates more qualified leads.",
        "system_prompt": _BACKBONE + " Style: narrative. Place the reader in the home; show, don't tell; sincere, no hype or cliches.",
        "paragraph_instruction": "4-5 paragraphs, ~200-250 words. A flowing narrative grounded in specific real details.",
    },
    "audience_first": {
        "id": "audience_first",
        "label": "Audience-First",
        "definition": "Tailors the opening to the likely buyer; balances emotion and hard facts.",
        "backed_by": "Copywriting practice: audience targeting + feature-to-benefit framing.",
        "system_prompt": _BACKBONE + " Style: balanced. Infer the likely buyer from the data, open for them, then alternate emotional appeal with concrete facts.",
        "paragraph_instruction": "4-5 paragraphs, ~220 words. Connect each notable feature to a lifestyle outcome.",
    },
}

TEMPLATE_IDS = ["concise", "word_optimized", "aida", "story", "audience_first"]

def get_template(template_id: str) -> dict:
    return _TEMPLATES.get(template_id, _TEMPLATES["word_optimized"])
