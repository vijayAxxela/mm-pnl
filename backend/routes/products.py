# routes/products.py
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel, ConfigDict
from typing import List, Optional
from database.db import get_db, Product, Market

router = APIRouter()

# Pydantic schemas
class ProductCreate(BaseModel):
    alias: str
    displayFactor: int
    displayType: int
    expirationDate: int
    id: str
    lastTradeDate: int
    marketId: int
    name: str
    pointValue: float
    productFamilyId: str
    productId: str
    productSymbol: str
    productTypeId: int
    ricCode: str
    roundLotQty: int
    securityExchange: int
    securityId: str
    seriesTermId: int
    term: str
    tickSize: float
    tickSizeDenominator: int
    tickSizeNumerator: int
    tickValue: float


class ProductResponse(BaseModel):

    db_id: int

    alias: str | None
    displayFactor: int | None
    displayType: int | None
    expirationDate: int | None
    id: str | None
    lastTradeDate: int | None
    marketId: int | None
    name: str | None
    pointValue: float | None
    productFamilyId: str | None
    productId: str | None
    productSymbol: str | None
    productTypeId: int | None
    ricCode: str | None
    roundLotQty: int | None
    securityExchange: int | None
    securityId: str | None
    seriesTermId: int | None
    term: str | None
    tickSize: float | None
    tickSizeDenominator: int | None
    tickSizeNumerator: int | None
    tickValue: float | None

    model_config = ConfigDict(from_attributes=True)

class MarketResponse(BaseModel):
    id: int
    tt_market_id: str
    name: str
    
    class Config:
        from_attributes = True


# ============= MARKET ENDPOINTS =============

@router.get("/markets", response_model=List[MarketResponse])
def get_all_markets(db: Session = Depends(get_db)):
    """Get all markets from database"""
    markets = db.query(Market).all()
    return markets

@router.get("/markets/by-name/{market_name}", response_model=MarketResponse)
def get_market_id_by_name(market_name: str, db: Session = Depends(get_db)):
    """Get market ID by market name (e.g., 'CME', 'ICE')"""
    market = db.query(Market).filter(Market.name == market_name).first()
    
    if not market:
        raise HTTPException(status_code=404, detail=f"Market '{market_name}' not found")
    
    return market

# ============= PRODUCT ENDPOINTS =============

@router.post("/", response_model=ProductResponse)
def create_product(product: ProductCreate, db: Session = Depends(get_db)):
    """Add a new TT product"""

    # use TT id for uniqueness
    existing_product = db.query(Product).filter(
        Product.id == product.id
    ).first()

    if existing_product:
        raise HTTPException(status_code=400, detail="Product already exists")

    db_product = Product(**product.model_dump(exclude_unset=True))

    db.add(db_product)
    db.commit()
    db.refresh(db_product)

    return db_product


@router.get("/", response_model=List[ProductResponse])
def get_products(db: Session = Depends(get_db)):
    """Get all products"""
    products = db.query(Product).all()
    return products

@router.get("/{product_id}", response_model=ProductResponse)
def get_product(product_id: int, db: Session = Depends(get_db)):
    """Get a specific product by ID"""
    product = db.query(Product).filter(Product.id == product_id).first()
    
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    
    return product

# @router.put("/{product_id}", response_model=ProductResponse)
# def update_product(product_id: int, product: ProductCreate, db: Session = Depends(get_db)):
#     """Update product details (tick_size, tick_value, etc.)"""
#     db_product = db.query(Product).filter(Product.id == product_id).first()
    
#     if not db_product:
#         raise HTTPException(status_code=404, detail="Product not found")
    
#     db_product.product_name = product.product_name
#     db_product.symbol = product.symbol
#     db_product.tick_size = product.tick_size
#     db_product.tick_value = product.tick_value
#     db_product.exchange = product.exchange
    
#     db.commit()
#     db.refresh(db_product)
    
#     return db_product

@router.delete("/{product_id}")
def delete_product(product_id: int, db: Session = Depends(get_db)):
    """Delete a product"""
    product = db.query(Product).filter(Product.id == product_id).first()
    
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    
    db.delete(product)
    db.commit()
    
    return {"message": "Product deleted successfully"}

