import os
from pathlib import Path
from typing import Optional

import psycopg
from psycopg.rows import dict_row
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

load_dotenv()

app = FastAPI(title="Newton Affordable Housing API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://localhost/newton_shi")


def get_conn():
    return psycopg.connect(DATABASE_URL, row_factory=dict_row)


@app.get("/", response_class=HTMLResponse)
def serve_frontend():
    html_path = Path(__file__).parent / "index.html"
    return HTMLResponse(content=html_path.read_text())


def build_where(q, ward, tenure, household_type, ami_tier):
    conditions = []
    params = []
    if q:
        conditions.append("(p.address ILIKE %s OR p.property_name ILIKE %s OR o.name ILIKE %s)")
        like = f"%{q}%"
        params += [like, like, like]
    if ward is not None:
        conditions.append("p.ward = %s")
        params.append(ward)
    if tenure:
        conditions.append("p.tenure = %s")
        params.append(tenure)
    if household_type:
        conditions.append("p.household_type = %s")
        params.append(household_type)
    if ami_tier:
        ami_col = {"30":"p.units_30pct_ami","50":"p.units_50pct_ami","60":"p.units_60pct_ami","80":"p.units_80pct_ami","120":"p.units_80_120pct_ami"}.get(ami_tier)
        if ami_col:
            conditions.append(f"{ami_col} > 0")
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    return where, params


@app.get("/api/summary")
def get_summary(q: Optional[str]=None, ward: Optional[int]=None, tenure: Optional[str]=None, household_type: Optional[str]=None, ami_tier: Optional[str]=None):
    where, params = build_where(q, ward, tenure, household_type, ami_tier)
    sql = f"""
        SELECT COUNT(*) AS total_properties,
               COALESCE(SUM(units_on_shi),0) AS total_shi_units,
               COALESCE(SUM(total_affordable_units),0) AS total_affordable_units,
               COUNT(DISTINCT organization_id) AS total_organizations,
               COALESCE(SUM(units_30pct_ami),0) AS ami30,
               COALESCE(SUM(units_50pct_ami),0) AS ami50,
               COALESCE(SUM(units_60pct_ami),0) AS ami60,
               COALESCE(SUM(units_80pct_ami),0) AS ami80,
               COALESCE(SUM(units_80_120pct_ami),0) AS ami120,
               COALESCE(SUM(affordable_sros),0) AS sro,
               COALESCE(SUM(affordable_studio),0) AS studio,
               COALESCE(SUM(affordable_1br),0) AS br1,
               COALESCE(SUM(affordable_2br),0) AS br2,
               COALESCE(SUM(affordable_3br),0) AS br3,
               COALESCE(SUM(affordable_4br_plus),0) AS br4
        FROM properties p
        LEFT JOIN organizations o ON p.organization_id = o.id
        {where}
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return dict(cur.fetchone())


@app.get("/api/properties")
def get_properties(q: Optional[str]=None, ward: Optional[int]=None, tenure: Optional[str]=None, household_type: Optional[str]=None, ami_tier: Optional[str]=None, sort: Optional[str]="address", direction: Optional[str]="asc"):
    allowed = {"address","organization","ward","tenure","total_units","units_on_shi","total_affordable_units"}
    if sort not in allowed:
        sort = "address"
    dir_sql = "DESC" if direction == "desc" else "ASC"
    sort_col = "o.name" if sort == "organization" else f"p.{sort}"
    where, params = build_where(q, ward, tenure, household_type, ami_tier)
    sql = f"""
        SELECT p.id, p.property_name, p.address, p.ward, p.tenure, p.household_type,
               p.total_units, p.units_on_shi, p.total_affordable_units, p.shi_pct, p.permit_type,
               p.affordable_sros AS sro, p.affordable_studio AS studio,
               p.affordable_1br AS br1, p.affordable_2br AS br2,
               p.affordable_3br AS br3, p.affordable_4br_plus AS br4,
               p.units_30pct_ami AS ami30, p.units_50pct_ami AS ami50,
               p.units_60pct_ami AS ami60, p.units_80pct_ami AS ami80,
               p.units_80_120pct_ami AS ami120,
               p.permit_date, p.full_occupancy_date, p.notes,
               o.name AS organization
        FROM properties p
        LEFT JOIN organizations o ON p.organization_id = o.id
        {where}
        ORDER BY {sort_col} {dir_sql} NULLS LAST
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]


@app.get("/api/properties/{property_id}")
def get_property(property_id: int):
    sql = """
        SELECT p.*, o.name AS organization, o.contact_name, o.contact_email, o.contact_phone
        FROM properties p
        LEFT JOIN organizations o ON p.organization_id = o.id
        WHERE p.id = %s
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (property_id,))
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Property not found")
            return dict(row)
