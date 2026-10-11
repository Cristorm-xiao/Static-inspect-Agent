"""SARIF 2.1.0 export for deterministic rule findings."""

from rules import get_rules


def to_sarif(findings: list[dict]) -> dict:
    definitions = {entry["rule_id"]: entry for entry in get_rules()}
    definitions["E000"] = {"rule_id": "E000", "name": "syntax_error", "severity": "error"}
    used_ids = sorted({item["rule_id"] for item in findings})
    rules = [{"id": rule_id, "name": definitions[rule_id]["name"],
              "shortDescription": {"text": definitions[rule_id]["name"]}}
             for rule_id in used_ids]
    level = {"error": "error", "warning": "warning", "info": "note"}
    results = []
    for item in findings:
        location = {"artifactLocation": {"uri": item["file"], "uriBaseId": "%SRCROOT%"}}
        if isinstance(item.get("line"), int) and item["line"] > 0:
            location["region"] = {"startLine": item["line"]}
        results.append({
            "ruleId": item["rule_id"],
            "level": level.get(item.get("severity"), "warning"),
            "message": {"text": item["desc"]},
            "locations": [{"physicalLocation": location}],
        })
    return {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [{"tool": {"driver": {"name": "static-inspect-agent", "rules": rules}},
                  "results": results}],
    }
