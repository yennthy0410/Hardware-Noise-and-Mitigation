"""Typed, fail-closed offline tools; no model-generated shell or IBM execution."""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CampaignRequest:
    project: Path
    campaign: str = 'vqe-independent-n-01'

    def __post_init__(self):
        if not isinstance(self.campaign,str) or not self.campaign or any(c in self.campaign for c in '/\\:'):
            raise ValueError('Campaign must be one safe identifier')


def comparison_readiness(request: CampaignRequest):
    """Read canonical STEP, accepted-slot and paired-export evidence without writes."""
    from independent_n_campaign import _campaign_completion_verification
    from job_registry import JobRegistry
    registry=JobRegistry(JobRegistry.default_database(request.project),readonly=True)
    status=registry.independent_campaign_status(request.campaign)
    evidence=_campaign_completion_verification(request.project,registry,status)
    return dict(state='READY_TO_COMPARE' if evidence['ready'] else 'BLOCKED',
                ready=evidence['ready'],campaign=request.campaign,
                verified_n_levels=evidence['verified_n_levels'],blockers=evidence['failures'],
                levels=evidence.get('levels',[]),missing_export_api=[],network_calls=0,production_writes=0)


TOOLS = {'campaign-comparison': comparison_readiness}


def invoke(name, request):
    if type(request) is not CampaignRequest:
        raise TypeError('Expected CampaignRequest')
    if name not in TOOLS:
        raise ValueError('Unregistered tool; arbitrary commands are forbidden')
    return TOOLS[name](request)


def load_skill(name):
    """Load only a selected allowlisted skill, not all skills into model context."""
    allowed = {'workflow-manager','campaign-comparison','dependence-aware-statistics'}
    if name not in allowed: raise ValueError('Unknown skill')
    return (Path(__file__).resolve().parents[1]/'02_skills'/name/'SKILL.md').read_text(encoding='utf-8')
