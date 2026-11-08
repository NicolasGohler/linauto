"""Do-not-contact rule matching and persistence."""
import asyncio
from types import SimpleNamespace

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from releasi.safety import suppression as sup


def lead(**kw):
    base = dict(company=None, email=None, linkedin_url=None)
    base.update(kw)
    return SimpleNamespace(**base)


RULES = sup.Rules(
    companies={"peripass", "solasecurity", "shopos"},
    domains={"peripass.com", "shoposhq.com"},
    urls={"linkedin.com/in/someone"},
)


def test_company_match_ignores_case_and_punctuation():
    assert RULES.match(lead(company="Sola Security"))
    assert RULES.match(lead(company="PERIPASS"))
    assert RULES.match(lead(company="ShopOS"))


def test_colleague_matched_by_email_domain_incl_subdomain():
    assert RULES.match(lead(email="new.hire@peripass.com"))
    assert RULES.match(lead(email="x@mail.shoposhq.com"))


def test_personal_email_needs_company_or_url():
    assert RULES.match(lead(email="guy@gmail.com")) is None
    assert RULES.match(lead(email="guy@gmail.com", company="Sola Security"))


def test_url_match_normalised():
    assert RULES.match(lead(linkedin_url="https://www.linkedin.com/in/SomeOne/?trk=x"))


def test_no_false_positive_on_lookalikes():
    assert RULES.match(lead(company="Peripass Logistics Ltd")) is None
    assert RULES.match(lead(email="a@notperipass.com")) is None
    assert RULES.match(lead()) is None


def test_persistence_roundtrip():
    async def go():
        eng = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with async_sessionmaker(eng, expire_on_commit=False)() as s:
            await sup.add_rule(s, "company", "Sola Security", reason="replied")
            await sup.add_rule(s, "company", "Sola Security")  # idempotent
            await sup.add_rule(s, "domain", "https://www.sola.security/", reason="replied")
            assert await sup.check_lead(s, lead(email="n@sola.security"))
            assert await sup.check_lead(s, lead(company="sola security"))
            assert await sup.check_lead(s, lead(company="other")) is None
        await eng.dispose()
    asyncio.run(go())
