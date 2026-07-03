from app.services.listing_templates import TEMPLATE_IDS, get_template

def test_five_templates_exist():
    assert TEMPLATE_IDS == ["concise", "word_optimized", "aida", "story", "audience_first"]

def test_each_template_has_required_fields():
    for tid in TEMPLATE_IDS:
        t = get_template(tid)
        for f in ("id", "label", "definition", "backed_by", "system_prompt", "paragraph_instruction"):
            assert t.get(f), f"{tid} missing {f}"

def test_unknown_template_falls_back_to_word_optimized():
    assert get_template("nope")["id"] == "word_optimized"
