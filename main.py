import os
import hmac
from enum import Enum
from pathlib import Path
from typing import Optional
from datetime import date

import psycopg
from psycopg.rows import dict_row
from fastapi import FastAPI, HTTPException, Depends, Header
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, field_validator
from dotenv import load_dotenv

load_dotenv()

app = FastAPI(title="Newton Affordable Housing API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "PUT", "OPTIONS"],
    allow_headers=["*"],
)

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://localhost/newton_shi")
EDIT_PASSWORD = os.environ.get("EDIT_PASSWORD")


def get_conn():
    return psycopg.connect(DATABASE_URL, row_factory=dict_row)


def require_edit_auth(x_edit_password: Optional[str] = Header(default=None)):
    """Gate for write endpoints. Fails closed if no password is configured."""
    if not EDIT_PASSWORD:
        raise HTTPException(status_code=503, detail="Editing is not configured on this server")
    if not x_edit_password or not hmac.compare_digest(x_edit_password, EDIT_PASSWORD):
        raise HTTPException(status_code=401, detail="Invalid or missing edit password")


# ---------------------------------------------------------------------------
# Enums (mirror the PostgreSQL ENUM types exactly)
# ---------------------------------------------------------------------------
class TenureEnum(str, Enum):
    rental = "rental"
    ownership = "ownership"
    ownership_resale = "ownership_resale"
    rental_in_ownership = "rental_in_ownership"
    mixed = "mixed"


class HouseholdEnum(str, Enum):
    non_age_restricted = "non_age_restricted"
    seniors = "seniors"
    seniors_60plus = "seniors_60plus"
    seniors_with_disabilities = "seniors_with_disabilities"
    supportive = "supportive"
    supportive_dv = "supportive_dv"
    supportive_veterans = "supportive_veterans"
    supportive_disability = "supportive_disability"
    transitional_family = "transitional_family"
    other = "other"


class PermitEnum(str, Enum):
    comp_permit = "comp_permit"
    special_permit = "special_permit"
    comp_and_special_permit = "comp_and_special_permit"
    by_right = "by_right"
    by_right_resale = "by_right_resale"
    by_right_dover = "by_right_dover"
    state_housing_667 = "state_housing_667"
    state_housing_705 = "state_housing_705"
    state_housing_689 = "state_housing_689"
    federal_public_housing = "federal_public_housing"
    section_8_nc = "section_8_nc"
    unknown = "unknown"


# ---------------------------------------------------------------------------
# Update model. Every field optional: only fields actually sent get written
# (partial update), so omitting a field never nulls existing data.
# ---------------------------------------------------------------------------
class PropertyUpdate(BaseModel):
    organization_id: Optional[int] = None
    property_name: Optional[str] = None
    address: Optional[str] = None
    ward: Optional[int] = None
    tenure: Optional[TenureEnum] = None
    household_type: Optional[HouseholdEnum] = None
    permit_type: Optional[PermitEnum] = None
    on_shi: Optional[bool] = None
    total_units: Optional[int] = None
    units_on_shi: Optional[int] = None
    total_affordable_units: Optional[int] = None
    shi_pct: Optional[float] = None
    affordable_sros: Optional[int] = None
    affordable_studio: Optional[int] = None
    affordable_1br: Optional[int] = None
    affordable_2br: Optional[int] = None
    affordable_3br: Optional[int] = None
    affordable_4br_plus: Optional[int] = None
    units_30pct_ami: Optional[int] = None
    units_50pct_ami: Optional[int] = None
    units_60pct_ami: Optional[int] = None
    units_80pct_ami: Optional[int] = None
    units_80_120pct_ami: Optional[int] = None
    supportive_or_dv_units: Optional[int] = None
    permit_date: Optional[date] = None
    full_occupancy_date: Optional[date] = None
    shi_expiry_date: Optional[date] = None
    check_bedroom_mix_ok: Optional[bool] = None
    check_afford_eq_shi: Optional[bool] = None
    notes: Optional[str] = None

    @field_validator("ward")
    @classmethod
    def ward_range(cls, v):
        if v is not None and not (1 <= v <= 8):
            raise ValueError("ward must be between 1 and 8")
        return v

    @field_validator("address")
    @classmethod
    def address_nonempty(cls, v):
        if v is not None and not v.strip():
            raise ValueError("address cannot be empty")
        return v


# For creating a property, address and tenure are required (NOT NULL, no default).
# Everything else is inherited as optional, so omitted columns fall back to their
# database defaults (the smallint counts default to 0, on_shi defaults to true).
class PropertyCreate(PropertyUpdate):
    address: str
    tenure: TenureEnum


# Organizations: only `name` is required; the rest are optional contact details.
class OrganizationCreate(BaseModel):
    name: str
    contact_name: Optional[str] = None
    contact_email: Optional[str] = None
    contact_phone: Optional[str] = None
    notes: Optional[str] = None

    @field_validator("name")
    @classmethod
    def name_nonempty(cls, v):
        if not v.strip():
            raise ValueError("name cannot be empty")
        return v


# Columns whose parameters must be explicitly cast to their PostgreSQL ENUM type
ENUM_CASTS = {
    "tenure": "tenure_type",
    "household_type": "household_type",
    "permit_type": "permit_type",
}


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


@app.put("/api/properties/{property_id}")
def update_property(property_id: int, payload: PropertyUpdate, _: None = Depends(require_edit_auth)):
    data = payload.model_dump(exclude_unset=True)
    if not data:
        raise HTTPException(status_code=400, detail="No fields provided to update")

    set_parts = []
    params = []
    for col, val in data.items():
        if isinstance(val, Enum):
            val = val.value
        cast = ENUM_CASTS.get(col)
        set_parts.append(f"{col} = %s::{cast}" if cast else f"{col} = %s")
        params.append(val)
    params.append(property_id)

    sql = f"UPDATE properties SET {', '.join(set_parts)} WHERE id = %s RETURNING id"
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Property not found")
            conn.commit()
    return {"status": "ok", "id": property_id}


@app.get("/api/organizations")
def list_organizations():
    """Public list of organizations for dropdowns. id + name only — no contact PII."""
    sql = "SELECT id, name FROM organizations ORDER BY name ASC"
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
            return [dict(r) for r in cur.fetchall()]


@app.post("/api/organizations")
def create_organization(payload: OrganizationCreate, _: None = Depends(require_edit_auth)):
    data = payload.model_dump(exclude_unset=True)
    cols = list(data.keys())
    placeholders = ["%s"] * len(cols)
    params = list(data.values())
    sql = f"INSERT INTO organizations ({', '.join(cols)}) VALUES ({', '.join(placeholders)}) RETURNING id, name"
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            row = cur.fetchone()
            conn.commit()
    return {"status": "ok", "id": row["id"], "name": row["name"]}


@app.post("/api/properties")
def create_property(payload: PropertyCreate, _: None = Depends(require_edit_auth)):
    data = payload.model_dump(exclude_unset=True)
    cols = []
    placeholders = []
    params = []
    for col, val in data.items():
        if isinstance(val, Enum):
            val = val.value
        cols.append(col)
        cast = ENUM_CASTS.get(col)
        placeholders.append(f"%s::{cast}" if cast else "%s")
        params.append(val)
    sql = f"INSERT INTO properties ({', '.join(cols)}) VALUES ({', '.join(placeholders)}) RETURNING id"
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            new_id = cur.fetchone()["id"]
            conn.commit()
    return {"status": "ok", "id": new_id}
