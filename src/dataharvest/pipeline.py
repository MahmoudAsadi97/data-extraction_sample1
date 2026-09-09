"""The extraction pipeline: extract -> normalise -> enrich -> verify -> validate -> dedupe -> deliver."""

from __future__ import annotations

import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from rapidfuzz import fuzz

from .config import ProjectConfig, env
from .enrich.search import WebSearch
from .enrich.vies import ViesClient, ViesResult
from .enrich.website import GENERIC_LOCALPARTS, SiteExtraction, WebsiteEnricher
from .http import HttpClient, dns_healthy
from .models import DuplicateGroup, FieldStatus, Record, RecordStatus
from .processing.dedupe import dedupe
from .processing.normalize import email_domain, name_key, url_domain
from .processing.validate import normalize_record, validate_record
from .processing.verify import (
    MailDomainChecker,
    assign_record_status,
    compute_completeness,
    verify_email_field,
    verify_phone_field,
)
from .report import RunReport
from .schema import Schema
from .sources import SourceError, get_source_class

log = logging.getLogger(__name__)

ProgressFn = Callable[[str, int, int], None]


@dataclass
class PipelineResult:
    records: list[Record]
    groups: list[DuplicateGroup]
    report: RunReport
    outputs: dict[str, Path] = field(default_factory=dict)

    @property
    def delivered(self) -> list[Record]:
        return [r for r in self.records if r.status != RecordStatus.EXCLUDED]


class Pipeline:
    def __init__(self, config: ProjectConfig, *, limit: int | None = None, offline: bool = False,
                 no_cache: bool = False, progress: ProgressFn | None = None, http: HttpClient | None = None) -> None:
        self.config = config
        self.schema: Schema = config.schema_def
        self.limit = limit or (config.max_records or None)
        self.offline = offline
        self.progress = progress or (lambda stage, done, total: None)
        self.report = RunReport(project_name=config.project.name, project_title=config.project.title)
        self.country = config.project.country
        cache_dir = config.resolve_path(config.http.cache_dir)
        self.http = http or HttpClient(
            user_agent=config.http.user_agent,
            contact_email=config.http.contact_email or env("DATAHARVEST_CONTACT_EMAIL"),
            timeout=config.http.timeout,
            cache=config.http.cache and not no_cache,
            cache_dir=cache_dir,
            cache_expire_hours=config.http.cache_expire_hours,
            min_delay_per_host=config.http.min_delay_per_host,
            max_retries=config.http.max_retries,
            respect_robots=config.enrichment.website.respect_robots,
        )
        self.records: list[Record] = []
        self.groups: list[DuplicateGroup] = []
        self.dns_broken = False

    # ================================================================== orchestration
    def run(self, export: bool = True) -> PipelineResult:
        cfg = self.config
        self.report.settings = {
            "schema": self.schema.name,
            "country": self.country,
            "sources": [s.label for s in cfg.active_sources],
            "limit": self.limit,
            "offline": self.offline,
            "enrichment": cfg.enrichment.model_dump(),
            "verification": cfg.verification.model_dump(),
            "dedupe": cfg.dedupe.model_dump(),
        }
        self.extract()
        self.normalise()
        if not self.offline:
            self.enrich_websites()
            self.find_missing_websites()
            self.enrich_apollo()
            self.check_vat_numbers()
        self.verify()
        self.validate()
        self.deduplicate()
        self.finalise()
        outputs: dict[str, Path] = {}
        if export:
            outputs = self.export()
        return PipelineResult(records=self.records, groups=self.groups, report=self.report, outputs=outputs)

    # ================================================================== stages
    def extract(self) -> None:
        stage = self.report.start("extract")
        total_sources = len(self.config.active_sources)
        for i, src_cfg in enumerate(self.config.active_sources, start=1):
            self.progress("extract", i - 1, total_sources)
            try:
                cls = get_source_class(src_cfg.type)
                source = cls(src_cfg, self.http, self.config)
                available, reason = cls.availability()
                if not available:
                    self.report.warn(f"source '{src_cfg.label}' skipped: {reason}")
                    continue
                per_source_limit = src_cfg.limit or self.limit
                count = 0
                for raw in source.extract(limit=per_source_limit):
                    self.records.append(Record.from_raw(raw))
                    count += 1
                    if self.limit and len(self.records) >= self.limit:
                        break
                stage.notes.append(f"{src_cfg.label}: {count}")
                for w in source.warnings:
                    self.report.warn(f"{src_cfg.label}: {w}")
            except SourceError as exc:
                self.report.warn(f"source '{src_cfg.label}' failed: {exc}")
            except Exception as exc:  # keep going with other sources, but record it
                log.exception("source %s crashed", src_cfg.label)
                self.report.warn(f"source '{src_cfg.label}' crashed: {exc}")
            if self.limit and len(self.records) >= self.limit:
                break
        self.progress("extract", total_sources, total_sources)
        self.report.finish(stage, len(self.records))
        if not self.records:
            raise SourceError("no records were extracted - check the warnings above and the project configuration")

    def normalise(self) -> None:
        stage = self.report.start("normalise", len(self.records))
        for rec in self.records:
            normalize_record(rec, self.schema, self.country)
        # stable, human-friendly ids: sorted by name, numbered once (kept through dedupe and export)
        name_field = self.schema.name_field or "company_name"
        self.records.sort(key=lambda r: (str(r.get(name_field) or "~").lower(), r.source_id))
        prefix = self.config.output.id_prefix or _id_prefix(self.config.project.name)
        for i, rec in enumerate(self.records, start=1):
            rec.record_id = f"{prefix}-{i:04d}"
        self.report.finish(stage, len(self.records))

    # ------------------------------------------------------------------ website enrichment
    def enrich_websites(self) -> None:
        wcfg = self.config.enrichment.website
        if not wcfg.enabled or not self.schema.field("website"):
            return
        targets = [r for r in self.records if r.get("website") and r.get(self.schema.name_field or "company_name")]
        stage = self.report.start("enrich: websites", len(targets))
        if targets and not dns_healthy():
            self.dns_broken = True
            self.report.warn("DNS resolution is failing on this machine, so websites cannot be checked; they are left 'unverified' "
                             "(on WSL/VPN see the Troubleshooting section of the README)")
        self._analyse_sites(targets, wcfg.workers, stage_name="enrich: websites")
        dns_failed = sum(1 for r in targets if (r.checks.get("website") or {}).get("liveness") == "unchecked")
        if dns_failed:
            self.report.warn(f"{dns_failed} website(s) could not be checked because name resolution failed on this machine "
                             f"(WSL/VPN?) - they are marked 'unverified', not 'dead'. Re-run once DNS works; see README Troubleshooting")
        self.report.finish(stage, len(targets))

    def _analyse_sites(self, targets: list[Record], workers: int, stage_name: str) -> None:
        wcfg = self.config.enrichment.website
        enricher = WebsiteEnricher(self.http, country=self.country, max_pages=wcfg.max_pages_per_site,
                                   timeout=wcfg.timeout, respect_robots=wcfg.respect_robots)
        name_field = self.schema.name_field or "company_name"
        done = 0
        self.progress(stage_name, 0, len(targets))
        pool = ThreadPoolExecutor(max_workers=max(1, workers))
        futures = {pool.submit(enricher.analyse, str(r.get("website")), r.get(name_field)): r for r in targets}
        try:
            for fut in as_completed(futures):
                rec = futures[fut]
                try:
                    extraction = fut.result()
                    self._apply_site_extraction(rec, extraction)
                except Exception as exc:
                    log.warning("website enrichment failed for %s: %s", rec.get("website"), exc)
                    rec.mark("website", FieldStatus.UNVERIFIED, f"enrichment error: {str(exc)[:80]}")
                done += 1
                self.progress(stage_name, done, len(targets))
        except KeyboardInterrupt:
            pool.shutdown(wait=False, cancel_futures=True)  # stop queued fetches immediately on Ctrl+C
            raise
        pool.shutdown(wait=True)

    def _apply_site_extraction(self, rec: Record, site: SiteExtraction) -> None:
        rec.checks["website"] = {
            "liveness": site.liveness, "http_status": site.status, "final_url": site.final_url, "error": site.error,
            "title": site.title, "pages": site.pages_fetched, "name_match": site.name_match, "name_similarity": site.name_similarity,
            "emails_found": site.emails, "phones_found": site.phones, "socials_found": site.socials, "vat_found": site.vat_numbers,
        }
        website_source = rec.fields["website"].source if "website" in rec.fields else ""
        via_search = website_source.startswith("search")
        if not site.ok:
            detail = site.error or f"HTTP {site.status}"
            if site.liveness == "blocked" or site.liveness == "unchecked" or (site.dns_failure and self.dns_broken):
                reason = "name resolution failed on this machine" if site.dns_failure else detail
                rec.mark("website", FieldStatus.UNVERIFIED, f"could not be checked automatically ({reason})")
                rec.flag(f"note: website could not be checked automatically ({reason}) - open it manually")
                return
            rec.mark("website", FieldStatus.INVALID, f"{site.liveness}: {detail}")
            rec.flag(f"website unreachable ({detail})")
            return
        if site.error:  # ok but not usable (e.g. PDF, empty)
            rec.mark("website", FieldStatus.UNVERIFIED, f"live but {site.error}")
            return
        site_domain = url_domain(site.final_url)
        note_parts = [f"live (HTTP {site.status})"]
        if site.note:
            note_parts.append(site.note)
        if site.liveness == "redirected":
            note_parts.append(f"redirects to {site.final_url}")
            rec.add_candidate("website", site.final_url)
        if site.name_match:
            note_parts.append("company name found on page")
            rec.mark("website", FieldStatus.VERIFIED, "; ".join(note_parts), append_note=False)
            name_field = self.schema.name_field or "company_name"
            rec.mark(name_field, FieldStatus.VERIFIED, "confirmed on company website")
            if via_search:
                rec.fields["website"].note += " (found via web search, confirmed)"
                rec.flags = [f for f in rec.flags if "found via web search" not in f]
        else:
            note_parts.append(f"company name not found on page (similarity {site.name_similarity}%)")
            rec.mark("website", FieldStatus.UNVERIFIED, "; ".join(note_parts), append_note=False)
            if via_search or site.name_similarity < 60 or site.liveness == "redirected":
                rec.flag("website could not be linked to the company name - review")

        # ---- e-mail
        own_emails = [e for e in site.emails if site_domain and (email_domain(e) or "").endswith(site_domain)]
        current_email = rec.get("email")
        if current_email:
            if str(current_email).lower() in site.emails:
                rec.mark("email", FieldStatus.VERIFIED, "confirmed on company website", append_note=False)
            elif own_emails:
                for e in own_emails[:3]:
                    rec.add_candidate("email", e)
                rec.mark("email", FieldStatus.CONFLICT, f"website lists {', '.join(own_emails[:2])} instead")
                rec.flag(f"e-mail differs from the company website ({own_emails[0]}) - review")
            else:
                rec.mark("email", FieldStatus.UNVERIFIED, "not found on company website")
        elif site.emails:
            best = site.emails[0]
            generic = best.split("@")[0].lower() in GENERIC_LOCALPARTS
            if best in own_emails or generic:
                rec.set("email", best, source="website", status=FieldStatus.VERIFIED, note="found on company website")
            else:
                rec.set("email", best, source="website", status=FieldStatus.UNVERIFIED,
                        note="found on company website (third-party mail domain; may be a personal or agency address)")
            for e in site.emails[1:4]:
                rec.add_candidate("email", e)

        # ---- phone
        current_phone = rec.get("phone")
        if current_phone:
            if current_phone in site.phones:
                rec.mark("phone", FieldStatus.VERIFIED, "confirmed on company website", append_note=False)
            elif site.phones:
                for p in site.phones[:3]:
                    rec.add_candidate("phone", p)
                rec.mark("phone", FieldStatus.UNVERIFIED, f"website lists other number(s): {', '.join(site.phones[:2])}")
        elif site.phones:
            rec.set("phone", site.phones[0], source="website", status=FieldStatus.VERIFIED, note="found on company website")
            for p in site.phones[1:3]:
                rec.add_candidate("phone", p)

        # ---- social profiles
        for network, link in site.socials.items():
            if not self.schema.field(network):
                continue
            current = rec.get(network)
            if not current:
                rec.set(network, link, source="website", status=FieldStatus.VERIFIED, note="linked from company website")
            elif str(current).rstrip("/").lower() == link.rstrip("/").lower():
                rec.mark(network, FieldStatus.VERIFIED, "confirmed on company website", append_note=False)
            else:
                rec.add_candidate(network, link)
                rec.mark(network, FieldStatus.UNVERIFIED, f"website links to {link}")

        # ---- VAT number
        if self.schema.field("vat_number") and site.vat_numbers:
            current_vat = rec.get("vat_number")
            if not current_vat:
                rec.set("vat_number", site.vat_numbers[0], source="website", status=FieldStatus.UNVERIFIED, note="found on company website")
            elif current_vat in site.vat_numbers:
                rec.mark("vat_number", FieldStatus.VERIFIED, "confirmed on company website", append_note=False)
            else:
                rec.add_candidate("vat_number", site.vat_numbers[0])
                rec.mark("vat_number", FieldStatus.CONFLICT, f"website shows {site.vat_numbers[0]}")
                rec.flag(f"VAT number differs from the company website ({site.vat_numbers[0]}) - review")

        # ---- description
        if self.schema.field("description") and not rec.get("description") and site.description:
            rec.set("description", site.description, source="website", status=FieldStatus.UNVERIFIED, note="meta description of the website")

    # ------------------------------------------------------------------ search for missing websites
    def find_missing_websites(self) -> None:
        scfg = self.config.enrichment.find_missing_websites
        if not scfg.enabled or scfg.provider == "off" or not self.schema.field("website"):
            return
        name_field = self.schema.name_field or "company_name"
        targets = [r for r in self.records if not r.get("website") and r.get(name_field)]
        if not targets:
            return
        if scfg.max_queries and len(targets) > scfg.max_queries:
            self.report.warn(f"{len(targets)} records have no website; searching for the first {scfg.max_queries} only (enrichment.find_missing_websites.max_queries)")
            targets = targets[: scfg.max_queries]
        search = WebSearch(self.http, provider=scfg.provider, delay=scfg.delay_seconds)
        stage = self.report.start(f"enrich: web search ({search.provider})", len(targets))
        found: list[Record] = []
        for i, rec in enumerate(targets, start=1):
            self.progress("enrich: web search", i - 1, len(targets))
            if not search.available:
                break
            hit = search.find_website(str(rec.get(name_field)), rec.get("city"), self.country)
            if hit:
                rec.set("website", hit.url, source=f"search:{hit.provider}", status=FieldStatus.UNVERIFIED,
                        note=f"found via web search: {hit.title[:80]}")
                rec.flag("website found via web search - verify")
                found.append(rec)
        self.progress("enrich: web search", len(targets), len(targets))
        if search.disabled_reason:
            self.report.warn(f"web search stopped: {search.disabled_reason}")
        self.report.finish(stage, len(found), f"{search.queries} queries, {len(found)} candidate websites")
        if found and self.config.enrichment.website.enabled:
            stage2 = self.report.start("enrich: websites (search results)", len(found))
            self._analyse_sites(found, self.config.enrichment.website.workers, stage_name="enrich: websites (search results)")
            self.report.finish(stage2, len(found))

    # ------------------------------------------------------------------ Apollo (optional, API key)
    def enrich_apollo(self) -> None:
        acfg = self.config.enrichment.apollo
        if not acfg.enabled:
            return
        if not env("APOLLO_API_KEY"):
            self.report.warn("Apollo enrichment is enabled but APOLLO_API_KEY is not set - skipped")
            return
        from .sources.apollo import enrich_by_domain

        wanted = [f for f in ("linkedin", "industry", "employees", "founded", "description", "phone", "facebook", "subcategory") if self.schema.field(f)]
        targets = [r for r in self.records if r.get("website") and any(not r.get(f) for f in wanted)]
        if not targets:
            return
        if acfg.max_lookups and len(targets) > acfg.max_lookups:
            self.report.warn(f"Apollo: {len(targets)} candidates, looking up the first {acfg.max_lookups} (enrichment.apollo.max_lookups)")
            targets = targets[: acfg.max_lookups]
        stage = self.report.start("enrich: Apollo", len(targets))
        hits = 0
        for i, rec in enumerate(targets, start=1):
            self.progress("enrich: Apollo", i - 1, len(targets))
            data = enrich_by_domain(self.http, url_domain(str(rec.get("website"))) or "")
            if not data:
                continue
            hits += 1
            for name in wanted:
                value = data.get(name) if name != "subcategory" else data.get("industry")
                if value in (None, ""):
                    continue
                if not rec.get(name):
                    rec.set(name, value, source="apollo", status=FieldStatus.UNVERIFIED, note="from Apollo.io company profile")
                elif name == "linkedin" and str(rec.get(name)).rstrip("/").lower() == str(value).rstrip("/").lower():
                    rec.mark(name, FieldStatus.VERIFIED, "confirmed by Apollo.io")
                else:
                    rec.add_candidate(name, value)
            rec.checks["apollo"] = {"matched": True, "domain": url_domain(str(rec.get("website")))}
        self.progress("enrich: Apollo", len(targets), len(targets))
        self.report.finish(stage, hits, f"{hits} profiles matched")

    # ------------------------------------------------------------------ VIES
    def check_vat_numbers(self) -> None:
        vcfg = self.config.enrichment.vies
        if not vcfg.enabled or not self.schema.field("vat_number"):
            return
        targets = [r for r in self.records if r.get("vat_number")]
        if not targets:
            return
        stage = self.report.start("verify: VIES VAT register", len(targets))
        client = ViesClient(self.http, delay=vcfg.delay_seconds)
        for i, rec in enumerate(targets, start=1):
            self.progress("verify: VIES", i - 1, len(targets))
            result = client.check(str(rec.get("vat_number")), self.country)
            self._apply_vies(rec, result)
        self.progress("verify: VIES", len(targets), len(targets))
        checked = sum(1 for r in targets if (r.checks.get("vies") or {}).get("checked"))
        self.report.finish(stage, checked, f"{client.requests_made} requests")

    def _apply_vies(self, rec: Record, result: ViesResult) -> None:
        rec.checks["vies"] = {"vat": result.vat, "valid": result.valid, "name": result.name, "address": result.address,
                              "error": result.error, "checked": result.checked}
        if result.vat and rec.get("vat_number") != result.vat:
            rec.set("vat_number", result.vat, source=rec.fields["vat_number"].source, status=rec.fields["vat_number"].status)
        if result.valid is True:
            note = f"valid in VIES{' - registered to ' + result.name if result.name else ''}"
            rec.mark("vat_number", FieldStatus.VERIFIED, note, append_note=False)
            if result.name and self.schema.field("legal_name") and not rec.get("legal_name"):
                rec.set("legal_name", result.name, source="vies", status=FieldStatus.VERIFIED, note="registered name in the VAT register")
            name_field = self.schema.name_field or "company_name"
            trade = name_key(str(rec.get(name_field) or ""))
            legal = name_key(result.name)
            if trade and legal and fuzz.token_set_ratio(trade, legal) >= 70:
                rec.mark(name_field, FieldStatus.VERIFIED, "matches the registered name in the VAT register")
            elif result.name:
                rec.flag(f"note: legal entity name ({result.name}) differs from trade name")
            postcode = rec.get("postcode")
            if postcode and result.postcode and str(postcode) != result.postcode:
                rec.flag(f"note: VAT registered address is {result.postcode} {result.city} (different postcode)")
        elif result.valid is False:
            rec.mark("vat_number", FieldStatus.INVALID, f"rejected by VIES ({result.error or 'not registered'})")
            rec.flag("VAT number is not valid according to the EU VIES register")
        else:
            rec.mark("vat_number", FieldStatus.UNVERIFIED, f"could not be checked ({result.error})")
            rec.flag(f"VAT number could not be verified ({result.error}) - unverified")

    # ------------------------------------------------------------------ verification, validation, dedupe
    def verify(self) -> None:
        vcfg = self.config.verification
        stage = self.report.start("verify: e-mail domains & phones", len(self.records))
        checker = MailDomainChecker(self.http) if (vcfg.email_mx and not self.offline) else None
        for i, rec in enumerate(self.records, start=1):
            self.progress("verify", i - 1, len(self.records))
            if self.schema.field("email"):
                verify_email_field(rec, checker)
            if self.schema.field("phone") and vcfg.phone_format:
                verify_phone_field(rec, self.country)
        self.progress("verify", len(self.records), len(self.records))
        note = f"{len(checker.cache)} mail domains looked up" if checker else "offline: mail domains not checked"
        self.report.finish(stage, len(self.records), note)

    def validate(self) -> None:
        stage = self.report.start("validate", len(self.records))
        problems = 0
        for rec in self.records:
            problems += len(validate_record(rec, self.schema, self.country))
        self.report.finish(stage, len(self.records), f"{problems} problem(s) flagged")

    def deduplicate(self) -> None:
        dcfg = self.config.dedupe
        if not dcfg.enabled:
            return
        stage = self.report.start("dedupe", len(self.records))
        for rec in self.records:
            compute_completeness(rec, self.schema)
        self.groups = dedupe(self.records, self.schema, threshold=dcfg.name_similarity, merge=dcfg.merge)
        merged = sum(1 for r in self.records if r.duplicate_of)
        self.report.finish(stage, len(self.records) - merged, f"{len(self.groups)} group(s), {merged} record(s) merged")

    def finalise(self) -> None:
        stage = self.report.start("finalise", len(self.records))
        for rec in self.records:
            compute_completeness(rec, self.schema)
            assign_record_status(rec, self.schema)
        self.records.sort(key=_sort_key)
        self.report.summarise(self.records, self.groups, self.schema)
        self.report.finish(stage, len([r for r in self.records if r.status != RecordStatus.EXCLUDED]))

    # ------------------------------------------------------------------ export
    def export(self) -> dict[str, Path]:
        from .export import export_all

        stage = self.report.start("export")
        outputs = export_all(self)
        self.report.outputs = {k: _display_path(v) for k, v in outputs.items()}
        self.report.finish(stage, len(outputs), ", ".join(outputs))
        directory, basename = self.output_paths()
        md, js = self.report.write(directory, basename)
        outputs["report_md"], outputs["report_json"] = md, js
        self.report.outputs.update({"report_md": _display_path(md), "report_json": _display_path(js)})
        return outputs

    def output_paths(self) -> tuple[Path, str]:
        directory = self.config.resolve_path(self.config.output.directory)
        stamp = datetime.now().strftime("%Y-%m-%d") if self.config.output.timestamp else ""
        basename = self.config.output.basename + (f"_{stamp}" if stamp else "")
        return directory, basename


def _display_path(path: Path) -> str:
    """Paths in reports are shown relative to the working directory when possible."""
    try:
        return str(Path(path).resolve().relative_to(Path.cwd().resolve()))
    except ValueError:
        return str(path)


def _id_prefix(project_name: str) -> str:
    letters = "".join(ch for ch in project_name.upper() if ch.isalpha())
    return (letters[:3] or "REC").ljust(3, "X")


def _sort_key(rec: Record) -> tuple:
    order = {RecordStatus.VERIFIED: 0, RecordStatus.PARTIALLY_VERIFIED: 1, RecordStatus.UNVERIFIED: 2,
             RecordStatus.NEEDS_REVIEW: 3, RecordStatus.EXCLUDED: 4}
    name = str(rec.get("company_name") or rec.get("name") or rec.get("title") or "").lower()
    return (order.get(rec.status, 9), name)


def run_project(config: ProjectConfig, **kwargs) -> PipelineResult:
    """Convenience wrapper: build and run a pipeline for a project configuration."""
    return Pipeline(config, **kwargs).run()
