---
{
  "kind": "verdict",
  "phase": "PRE_RUN",
  "handoff": "{{HANDOFF_REF}}",
  "subject": {
    "commit": "ff25847a583f3117ed2ee8e658a97b266e54f5a4",
    "report": "NONE",
    "results": []
  },
  "gates": [
    {
      "id": "G1",
      "verdict": "VALID",
      "evidence": [
        "smoke/scores.jsonl has 8 rows"
      ]
    },
    {
      "id": "G2",
      "verdict": "VALID",
      "evidence": [
        "git diff 252ff2f..ff25847 -- transplant/"
      ]
    }
  ]
}
---
## Defects

NONE
