from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List, Any, Dict
# from sqlalchemy.orm import Session
# from models import get_session, CustomObject

router = APIRouter(prefix="/api/paas", tags=["PaaS"])

# ── Pydantic Models ──
class SchemaFieldDef(BaseModel):
    name: str
    type: str
    label: str
    required: bool

class SchemaDefinition(BaseModel):
    object_name: str
    label: str
    fields: List[SchemaFieldDef]

class DynamicRecordPayload(BaseModel):
    data: Dict[str, Any]

# ── Endpoints ──
@router.get("/schema/{object_name}", response_model=SchemaDefinition)
def get_object_schema(object_name: str):
    """
    Returns the JSON schema required for the frontend to dynamically render forms/tables.
    Mocking a 'crypto_wallet' object query for demonstration.
    """
    if object_name != "crypto_wallet":
        raise HTTPException(status_code=404, detail="Object schema not found")
        
    return SchemaDefinition(
        object_name="crypto_wallet",
        label="Crypto Wallet",
        fields=[
            SchemaFieldDef(name="wallet_address", type="text", label="Wallet Address", required=True),
            SchemaFieldDef(name="balance", type="number", label="Balance", required=True),
            SchemaFieldDef(name="currency", type="text", label="Currency", required=True)
        ]
    )
