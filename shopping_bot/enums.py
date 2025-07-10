# shopping_bot/enums.py
"""
All strongly-typed Enum definitions used throughout the app.

Helps:
• Avoid magic strings
• Improve type-checking / auto-completion
"""

from enum import Enum, auto


class ShoppingFunction(str, Enum):
    ASK_USER_PREFERENCES   = "ask_user_preferences"
    ASK_USER_BUDGET        = "ask_user_budget"
    ASK_DELIVERY_ADDRESS   = "ask_delivery_address"
    FETCH_PRODUCT_INVENTORY = "fetch_product_inventory"
    FETCH_PURCHASE_HISTORY  = "fetch_purchase_history"
    FETCH_USER_PROFILE      = "fetch_user_profile"
    FETCH_PRODUCT_DETAILS   = "fetch_product_details"
    FETCH_SIMILAR_PRODUCTS  = "fetch_similar_products"
    FETCH_PRODUCT_REVIEWS   = "fetch_product_reviews"


class QueryIntent(str, Enum):
    PRODUCT_SEARCH     = "product_search"
    RECOMMENDATION     = "recommendation"
    PRICE_INQUIRY      = "price_inquiry"
    PURCHASE           = "purchase"
    ORDER_STATUS       = "order_status"
    PRODUCT_COMPARISON = "product_comparison"
    GENERAL_HELP       = "general_help"


class ResponseType(str, Enum):
    QUESTION     = "question"
    FINAL_ANSWER = "final_answer"
    ERROR        = "error"
