#!/usr/bin/env python3
"""
Test script for macro filtering implementation
Tests the entire flow from MacroOptimizer to ES query generation
"""

import sys
import json
from pathlib import Path

# Add shopping_bot to path
sys.path.insert(0, str(Path(__file__).parent))

from shopping_bot.macro_optimizer import get_macro_optimizer
from shopping_bot.data_fetchers.es_products import _build_enhanced_es_query


def test_macro_optimizer():
    """Test MacroOptimizer standalone"""
    print("=" * 80)
    print("TEST 1: MacroOptimizer - User specifies protein constraint")
    print("=" * 80)
    
    optimizer = get_macro_optimizer()
    
    # Test 1: User specifies protein > 20g
    result = optimizer.merge_constraints(
        user_constraints=[{
            "nutrient_name": "protein g",
            "operator": "gt",
            "value": 20
        }],
        category_path="f_and_b/food/light_bites/energy_bars"
    )
    
    print(f"\nUser constraints: protein > 20g")
    print(f"Category: energy_bars")
    print(f"Hard filters: {len(result['hard_filters'])}")
    for hf in result['hard_filters']:
        print(f"  - {hf['nutrient']} {hf['operator']} {hf['value']} (source: {hf['source']})")
    
    print(f"\nSoft boosts: {len(result['soft_boosts'])}")
    for sb in result['soft_boosts'][:5]:  # Show first 5
        print(f"  - {sb['nutrient']} {sb['operator']} {sb['value']} weight={sb['weight']} (source: {sb['source']})")
    
    print(f"\nhas_constraints: {result['has_constraints']}")
    
    assert result['has_constraints'] == True, "Should have constraints"
    assert len(result['hard_filters']) == 1, "Should have 1 hard filter"
    assert result['hard_filters'][0]['nutrient'] == 'protein g', "Hard filter should be protein"
    
    print("\n✅ Test 1 PASSED\n")
    
    # Test 2: No user constraints
    print("=" * 80)
    print("TEST 2: MacroOptimizer - No user constraints (should skip category defaults)")
    print("=" * 80)
    
    result2 = optimizer.merge_constraints(
        user_constraints=[],
        category_path="f_and_b/food/light_bites/chips_and_crisps"
    )
    
    print(f"\nUser constraints: None")
    print(f"Category: chips_and_crisps")
    print(f"Hard filters: {len(result2['hard_filters'])}")
    print(f"Soft boosts: {len(result2['soft_boosts'])}")
    print(f"has_constraints: {result2['has_constraints']}")
    
    assert result2['has_constraints'] == False, "Should have no constraints"
    assert len(result2['hard_filters']) == 0, "Should have no hard filters"
    assert len(result2['soft_boosts']) == 0, "Should have no soft boosts (no user constraints)"
    
    print("\n✅ Test 2 PASSED\n")
    
    # Test 3: Multi-constraint
    print("=" * 80)
    print("TEST 3: MacroOptimizer - Multiple constraints")
    print("=" * 80)
    
    result3 = optimizer.merge_constraints(
        user_constraints=[
            {"nutrient_name": "protein g", "operator": "gte", "value": 15},
            {"nutrient_name": "total sugar g", "operator": "lt", "value": 5}
        ],
        category_path="f_and_b/food/light_bites/chips_and_crisps"
    )
    
    print(f"\nUser constraints: protein >= 15g, sugar < 5g")
    print(f"Hard filters: {len(result3['hard_filters'])}")
    for hf in result3['hard_filters']:
        print(f"  - {hf['nutrient']} {hf['operator']} {hf['value']}")
    
    assert len(result3['hard_filters']) == 2, "Should have 2 hard filters"
    assert result3['has_constraints'] == True, "Should have constraints"
    
    print("\n✅ Test 3 PASSED\n")


def test_es_query_generation():
    """Test ES query generation with macro filters"""
    print("=" * 80)
    print("TEST 4: ES Query Generation - With macro constraints")
    print("=" * 80)
    
    params = {
        "q": "protein bars",
        "category_group": "f_and_b",
        "category_paths": ["f_and_b/food/light_bites/energy_bars"],
        "macro_filters": [
            {"nutrient_name": "protein g", "operator": "gt", "value": 20}
        ],
        "size": 10
    }
    
    query = _build_enhanced_es_query(params)
    
    print(f"\nGenerated ES query structure:")
    print(f"  - Query type: {list(query['query'].keys())}")
    print(f"  - Has function_score: {'function_score' in query['query']}")
    
    # Check if range filter for protein exists
    bool_query = query['query']['function_score']['query']['bool']
    filters = bool_query['filter']
    
    print(f"  - Total filters: {len(filters)}")
    
    has_protein_filter = False
    for f in filters:
        if 'range' in f:
            if 'category_data.nutritional.nutri_breakdown_updated.protein g' in f['range']:
                has_protein_filter = True
                print(f"  - ✅ Found protein range filter: {f['range']}")
    
    assert has_protein_filter, "Should have protein range filter"
    
    print("\n✅ Test 4 PASSED\n")
    
    # Test 5: No macro constraints
    print("=" * 80)
    print("TEST 5: ES Query Generation - Without macro constraints")
    print("=" * 80)
    
    params2 = {
        "q": "chips",
        "category_group": "f_and_b",
        "category_paths": ["f_and_b/food/light_bites/chips_and_crisps"],
        "macro_filters": [],  # Empty - no user constraints
        "size": 10
    }
    
    query2 = _build_enhanced_es_query(params2)
    
    # Check that NO macro filters were applied
    bool_query2 = query2['query']['function_score']['query']['bool']
    filters2 = bool_query2['filter']
    
    has_nutri_filter = False
    for f in filters2:
        if 'range' in f:
            field = list(f['range'].keys())[0] if f['range'] else ""
            if 'nutri_breakdown_updated' in field:
                has_nutri_filter = True
    
    print(f"  - Total filters: {len(filters2)}")
    print(f"  - Has nutritional filters: {has_nutri_filter}")
    
    assert not has_nutri_filter, "Should NOT have nutritional filters when no user constraints"
    
    print("\n✅ Test 5 PASSED\n")


def test_nutrient_normalization():
    """Test nutrient name normalization"""
    print("=" * 80)
    print("TEST 6: Nutrient Name Normalization")
    print("=" * 80)
    
    optimizer = get_macro_optimizer()
    
    test_cases = [
        ("protein", "protein g"),
        ("sodium", "sodium mg"),
        ("calories", "energy kcal"),
        ("fiber", "fiber g"),
        ("saturated fat", "saturated fat g"),
    ]
    
    for user_input, expected in test_cases:
        result = optimizer.normalize_nutrient_name(user_input)
        print(f"  '{user_input}' → '{result}' (expected: '{expected}')")
        assert result == expected, f"Failed: {user_input} should normalize to {expected}"
    
    print("\n✅ Test 6 PASSED\n")


def main():
    """Run all tests"""
    print("\n" + "=" * 80)
    print("MACRO FILTERING IMPLEMENTATION TESTS")
    print("=" * 80 + "\n")
    
    try:
        test_macro_optimizer()
        test_es_query_generation()
        test_nutrient_normalization()
        
        print("\n" + "=" * 80)
        print("✅ ALL TESTS PASSED!")
        print("=" * 80 + "\n")
        
        print("Next steps:")
        print("1. Run the app and test with real queries:")
        print('   - "protein bars with more than 20g protein"')
        print('   - "chips with less than 200mg sodium"')
        print('   - "show me chips" (should use percentile ranking, NO macro filters)')
        print("\n2. Check debug logs for:")
        print("   - DEBUG: MACRO_FILTERING messages")
        print("   - DEBUG: MACRO_HARD_FILTER messages")
        print("   - DEBUG: MACRO_SOFT_BOOST messages")
        print("\n3. Verify ES queries in logs include range filters for nutrients")
        
    except Exception as e:
        print(f"\n❌ TEST FAILED: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()

