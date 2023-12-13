import csv
from typing import Dict, Optional, Any
import countrynames
from pantomime.types import CSV
from nomenklatura.util import is_qid

from zavod import Context
from zavod import helpers as h
from zavod.entity import Entity
from zavod.logic.pep import PositionCategorisation, categorise
from zavod.util import remove_emoji

from zavod.shed.wikidata.query import run_query, CACHE_MEDIUM


def keyword(topics: [str]) -> Optional[str]:
    if "gov.national" in topics:
        return "National government"
    if "gov.state" in topics:
        return "State government"
    if "gov.igo" in topics:
        return "International organization"
    return None


def date_value(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    if len(text) < 4:
        return None
    return text[:10]


def truncate_date(text: Optional[str]) -> Optional[str]:
    if text is None:
        return None
    return text[:10]


def crawl_holder(context: Context, categorisation: PositionCategorisation, position: Entity, holder: Dict[str, str]) -> None:
    # print(holder)
    entity = context.make("Person")
    qid: Optional[str] = holder.get("person_qid")
    if not is_qid(qid) or qid == "Q1045488":
        return
    entity.id = qid

    occupancy = h.make_occupancy(
        context,
        entity,
        position,
        False,
        death_date=date_value(holder.get("person_death")),
        birth_date=date_value(holder.get("person_birth")),
        end_date=date_value(holder.get("end_date")),
        start_date=date_value(holder.get("start_date")),
        categorisation=categorisation,
    )
    if not occupancy:
        return

    # TODO: decide all entities with no P39 dates as false?
    # print(holder.person_qid, death, start_date, end_date)

    if holder.get("person_label") != qid:
        entity.add("name", holder.get("person_label"))
    entity.add("keywords", keyword(categorisation.topics))

    context.emit(position)
    context.emit(occupancy)
    context.emit(entity, target=True)


def query_position_holders(context: Context, categorisation: PositionCategorisation) -> None:
    vars = {"POSITION": categorisation.entity_id}
    context.log.info(f"Crawling holders of position [{categorisation.entity_id}]: {categorisation.caption}")
    response = run_query(context, "holders/holders", vars, cache_days=CACHE_MEDIUM)
    for binding in response.results:
        start_date = truncate_date(
            binding.plain("positionStart")
            or binding.plain("bodyStart")
            or binding.plain("bodyInception")
        )
        end_date = truncate_date(
            binding.plain("positionEnd")
            or binding.plain("bodyEnd")
            or binding.plain("bodyAbolished")
        )
        
        yield {
            "person_qid": binding.plain("person"),
            "person_label": binding.plain("personLabel"),
            "person_description": binding.plain("personDescription"),
            "person_birth": truncate_date(binding.plain("birth")),
            "person_death": truncate_date(binding.plain("death")),
            "start_date": start_date,
            "end_date": end_date,
        }


def pick_country(context, *qids):

    for qid in qids:
        country = context.lookup("country_decisions", qid)
        if country is not None and country.decision == "national":
            return country
    return None


def query_positions(context: Context, country):
    context.log.info(f"Crawling positions: {country['label']} ({country['qid']})")
    vars = {"COUNTRY": country["qid"]}
    # a) All positions by jurisdiction/country
    response = run_query(context, "positions/country", vars, cache_days=CACHE_MEDIUM)
    for bind in response.results:
        country_res = pick_country(
            context,
            bind.plain("country"),
            bind.plain("jurisdiction"),
            country["qid"],
        )
        yield {
            "qid": bind.plain("position"),
            "label": bind.plain("positionLabel"),
            "description": bind.plain("positionDescription"),
            "country_code": country_res.code
        }

    # b) Positions held by politicans from that country
    response = run_query(context, "positions/politician", vars, cache_days=CACHE_MEDIUM)
    for bind in response.results:
        country = pick_country(
            context,
            bind.plain("country"),
            bind.plain("jurisdiction"),
        )
        qid = bind.plain("position")
        label = bind.plain('positionLabel')
        yield {
            "qid": qid,
            "label": label,
            "description": bind.plain("positionDescription"),
            "country_code": country_res.code if country_res else None,
        }


def query_countries(context: Context):
    response = run_query(context, "countries/all")
    for binding in response.results:
        qid = binding.plain("country")
        label = binding.plain("countryLabel")
        if qid is None or qid == label:
            continue
        code = countrynames.to_code(label)
        yield {
            "qid": qid,
            "code": code,
            "label": label,
            "description": binding.plain("countryDescription"),
        }


def crawl(context: Context):
    completed_positions = set()
    for country in query_countries(context):
        context.log.info(f"Crawling country: {country['qid']} ({country['label']})")

        country_res = context.lookup("country_decisions", country["qid"])
        if country_res is None:
            context.log.warning("Country without decision", country=country)
            continue
        if country_res.decision != "national":
            continue
        
        for wd_position in query_positions(context, country):
            if wd_position["qid"] in completed_positions:
                continue
            # TODO: geting 500 from wikidata when querying this - might be an issue with 
            # adding FILTER (isIRI(?position) && !wikibase:isSomeValue(?position))
            # to the query
            if wd_position["qid"] == "Q110086626":
                continue
            
            position = h.make_position(
                context,
                wd_position["label"],
                country=wd_position["country_code"],
                wikidata_id=wd_position["qid"],
            )
            categorisation = categorise(context, position, is_pep=None)
            if not categorisation.is_pep:
                continue

            for holder in query_position_holders(context, categorisation):
                crawl_holder(context, categorisation, position, holder)
            completed_positions.add(wd_position["qid"])

    entity = context.make("Person")
    entity.id = "Q21258544"
    entity.add("name", "Mark Lipparelli")
    entity.add("topics", "role.pep")
    entity.add("country", "us")
    context.emit(entity, target=True)


