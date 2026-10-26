"""Deterministic, no-API-key extraction.

Every finding produced here is a *verbatim sentence or heading from the fetched page* plus a label.
Nothing is paraphrased, so nothing can be invented. It is deliberately modest: it finds what the
website says; interpretation (fit) is labelled as an inference and left to the operator to accept.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.services.research.extract import Page, sentences

TAG_PATTERNS: dict[str, re.Pattern] = {
    "python_fastapi": re.compile(r"\b(python|fastapi|django|flask)\b", re.I),
    "ai_llm": re.compile(r"\b(artificial intelligence|machine learning|deep learning|generative ai|genai|llms?|large language models?|chatbots?|computer vision|nlp|natural language processing|ai|ml)\b", re.I),
    "rag_documents": re.compile(r"\b(rag|retrieval[- ]augmented|document (processing|intelligence|extraction|analysis)|ocr|knowledge base|semantic search|vector (search|database|store)|intelligent document)\b", re.I),
    "integrations": re.compile(r"\b(api integrations?|integrations?|workflow automation|business process automation|erp|webhooks?|middleware|ipaas|connectors?|rest apis?|data pipelines?|etl)\b", re.I),
    "aws_devops": re.compile(r"\b(aws|amazon web services|lambda|kubernetes|docker|devops|cloud migration|terraform|serverless|ci/cd)\b", re.I),
    "ai_testing": re.compile(r"\b(llm evaluation|model evaluation|ai testing|mlops|model monitoring|ai quality|test automation|quality assurance)\b", re.I),
}
TAG_LABEL = {
    "python_fastapi": "Python / FastAPI backend development",
    "ai_llm": "AI/ML and LLM integration",
    "rag_documents": "RAG and document processing",
    "integrations": "API and workflow integrations",
    "aws_devops": "Docker and AWS deployment",
    "ai_testing": "AI application testing and productionization",
}
HIRING_RE = re.compile(r"\b(we(?:'|’)re hiring|we are hiring|now hiring|open positions?|current openings?|join our team|we(?:'|’)re looking for|we are looking for|career opportunities|job openings?|apply now)\b", re.I)
ENG_ROLE_RE = re.compile(r"\b(engineer|developer|data scientist|ml|machine learning|ai|backend|devops|cloud|architect|software)\b", re.I)

TYPE_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("aws_consultancy", re.compile(r"\b(aws (advanced |select |premier )?(consulting )?partner|aws consult\w+|amazon web services (partner|consult\w+)|cloud consult\w+|apn partner)\b", re.I)),
    ("ai_consultancy", re.compile(r"\b(ai (consult\w+|agency|studio|solutions? (provider|company))|machine learning consult\w+|data science consult\w+|ai (and|&) data consult\w+|artificial intelligence consult\w+)\b", re.I)),
    ("software_agency", re.compile(r"\b(software (agency|development (company|agency|services|studio)|house|studio|consult\w+)|digital (agency|product studio)|custom software|web (development )?agency|app development (company|agency)|development partner|nearshore|outsourc\w+|product studio)\b", re.I)),
    ("product_company", re.compile(r"\b(our (platform|product|saas|software solution)|saas|start-?up|we (build|offer|provide) (a|an) (platform|product|tool))\b", re.I)),
]
COUNTRIES = {
    "germany": "DE", "deutschland": "DE", "france": "FR", "united kingdom": "GB", "england": "GB", "scotland": "GB", "italy": "IT", "italia": "IT",
    "spain": "ES", "españa": "ES", "switzerland": "CH", "schweiz": "CH", "suisse": "CH", "austria": "AT", "österreich": "AT",
    "netherlands": "NL", "nederland": "NL", "belgium": "BE", "belgique": "BE", "sweden": "SE", "sverige": "SE", "finland": "FI",
    "denmark": "DK", "norway": "NO", "ireland": "IE", "poland": "PL", "polska": "PL", "portugal": "PT", "luxembourg": "LU",
    "united states": "US", "usa": "US", "canada": "CA", "hungary": "HU", "czech republic": "CZ", "romania": "RO", "greece": "GR",
}


@dataclass
class FindingDraft:
    category: str
    statement: str
    kind: str = "fact"
    confidence: str = "medium"
    service_tags: list[str] = field(default_factory=list)
    evidence: list[tuple[int, str]] = field(default_factory=list)  # (source_id, verbatim quote)


@dataclass
class ExtractionOutput:
    findings: list[FindingDraft]
    company_type: str
    summary: str | None
    uncertainties: list[str]


def run_heuristics(pages: list[tuple[int, Page, str]]) -> ExtractionOutput:
    """pages: [(source_id, Page, source_type)] where source_type is official_site | search_result."""
    findings: list[FindingDraft] = []
    uncertainties: list[str] = []
    official = [(sid, p) for sid, p, st in pages if st == "official_site"]
    if not official:
        uncertainties.append("No page of the company's own website could be read, so nothing here is first-party.")
    home = official[0] if official else None

    # 1. what the company says it is
    if home:
        sid, p = home
        desc = p.meta_description
        if desc and len(desc) >= 40:
            findings.append(FindingDraft("description", desc, "fact", "high", evidence=[(sid, desc)]))
        else:
            for s in sentences(p.text, 60, 300)[:3]:
                if not p.injection_flag:
                    findings.append(FindingDraft("description", s, "fact", "medium", evidence=[(sid, s)]))
                    break
        if not findings:
            uncertainties.append("The home page has no description text to quote.")
    if any(p.injection_flag for _, p in official):
        uncertainties.append("A page contains text that looks like instructions to an AI. It was flagged and its content is untrusted.")

    # 2. services / products (headings on the relevant pages)
    heads: list[tuple[int, str]] = []
    for sid, p in official:
        for lvl, h in p.headings:
            if lvl in (2, 3) and 6 <= len(h) <= 80 and len(h.split()) <= 9 and not re.search(r"cookie|newsletter|follow us|privacy|subscribe|contact us|©", h, re.I):
                heads.append((sid, h))
    seen, uniq = set(), []
    for sid, h in heads:
        if h.lower() not in seen:
            seen.add(h.lower())
            uniq.append((sid, h))
    if uniq:
        top = uniq[:6]
        findings.append(FindingDraft("product_service", "The website lists these topics: " + "; ".join(h for _, h in top),
                                     "fact", "medium", evidence=[(sid, h) for sid, h in top]))

    # 3. technology / capability sentences per service tag
    per_tag: dict[str, int] = {}
    used: set[str] = set()
    for sid, p, src_type in pages:
        if p.injection_flag:
            continue
        for s in sentences(p.text):
            if s in used:
                continue
            for tag, rx in TAG_PATTERNS.items():
                if per_tag.get(tag, 0) >= 2 or not rx.search(s):
                    continue
                used.add(s)
                per_tag[tag] = per_tag.get(tag, 0) + 1
                findings.append(FindingDraft("technology", f"The website mentions: “{s}”", "fact",
                                             "medium" if src_type == "official_site" else "low", [tag], [(sid, s)]))
                break
    # 4. hiring signals
    hiring_done = 0
    for sid, p, _st in pages:
        if p.injection_flag or hiring_done >= 2:
            continue
        for s in sentences(p.text, 15):
            if HIRING_RE.search(s) and hiring_done < 2:
                eng = bool(ENG_ROLE_RE.search(s))
                findings.append(FindingDraft("hiring_signal", f"The website says: “{s}”", "fact", "medium", evidence=[(sid, s)]))
                hiring_done += 1
                if eng:
                    break
    for sid, p in official:
        roles = [h for lvl, h in p.headings if lvl in (2, 3) and ENG_ROLE_RE.search(h) and 6 <= len(h) <= 80]
        if roles and re.search(r"career|job|hiring|join", p.url, re.I):
            findings.append(FindingDraft("hiring_signal", "Open roles appear to include: " + "; ".join(roles[:4]), "fact", "medium",
                                         evidence=[(sid, r) for r in roles[:4]]))
            break

    # 5. location (for jurisdiction)
    for sid, p in official:
        low = p.text.lower()
        hit = next(((name, code) for name, code in COUNTRIES.items() if re.search(rf"\b{re.escape(name)}\b", low)), None)
        if hit:
            sent = next((s for s in sentences(p.text, 8, 300) if hit[0] in s.lower()), None)
            if sent:
                findings.append(FindingDraft("location", f"The website mentions {hit[0].title()} ({hit[1]}): “{sent}”", "fact", "low", evidence=[(sid, sent)]))
                break

    # 6. company type from the website's own wording
    company_type, type_ev = "unknown", None
    for ctype, rx in TYPE_PATTERNS:
        for sid, p in official:
            if p.injection_flag:
                continue
            for s in sentences(p.text, 15):
                if rx.search(s):
                    company_type, type_ev = ctype, (sid, s)
                    break
            if type_ev:
                break
        if type_ev:
            break
    if type_ev:
        findings.append(FindingDraft("business_model", f"Appears to be: {company_type.replace('_', ' ')}. The website says: “{type_ev[1]}”",
                                     "fact", "low", evidence=[type_ev]))

    # 7. possible fit (labelled inference, backed by the exact facts it rests on)
    by_tag: dict[str, list[FindingDraft]] = {}
    for f in findings:
        for t in f.service_tags:
            by_tag.setdefault(t, []).append(f)
    for tag, fs in sorted(by_tag.items(), key=lambda kv: -len(kv[1]))[:2]:
        ev = [e for f in fs for e in f.evidence][:2]
        findings.append(FindingDraft("fit_reason", f"Possible fit (an inference, not a stated need): the website talks about {TAG_LABEL[tag].lower()}, which is something the sender offers.",
                                     "inference", "low", [tag], ev))

    if not any(f.category in ("hiring_signal",) for f in findings):
        uncertainties.append("No hiring information was found on the pages read.")
    if not any(f.service_tags for f in findings):
        uncertainties.append("Nothing on the pages read clearly relates to the sender's services.")
    summary = None
    if home and home[1].meta_description:
        summary = home[1].meta_description
    return ExtractionOutput(findings, company_type, summary, uncertainties)
