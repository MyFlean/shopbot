from enum import Enum


class UserSlot(str, Enum):
    USER_PREFERENCES = "ASK_USER_PREFERENCES"
    USER_BUDGET = "ASK_USER_BUDGET"
    DELIVERY_ADDRESS = "ASK_DELIVERY_ADDRESS"
    PRODUCT_CATEGORY = "ASK_PRODUCT_CATEGORY"
    DIETARY_REQUIREMENTS = "ASK_DIETARY_REQUIREMENTS"
    USE_CASE = "ASK_USE_CASE"
    QUANTITY = "ASK_QUANTITY"
    ORDER_ID = "ASK_ORDER_ID"


class BackendFunction(str, Enum):
    # Primary function - all product searches go through this
    SEARCH_PRODUCTS = "search_products"
    
    # Keep these for future user-specific data
    FETCH_USER_PROFILE = "fetch_user_profile"
    FETCH_PURCHASE_HISTORY = "fetch_purchase_history"
    FETCH_ORDER_STATUS = "fetch_order_status"


class QueryIntent(str, Enum):
    PRODUCT_SEARCH = "product_search"
    RECOMMENDATION = "recommendation"
    PRICE_INQUIRY = "price_inquiry"
    PURCHASE = "purchase"
    ORDER_STATUS = "order_status"
    PRODUCT_COMPARISON = "product_comparison"
    GENERAL_HELP = "general_help"


class ResponseType(str, Enum):
    QUESTION = "question"
    PROCESSING_STUB = "processing"
    FINAL_ANSWER = "final_answer"
    ERROR = "error"


# Backwards-compat alias
ShoppingFunction = BackendFunction