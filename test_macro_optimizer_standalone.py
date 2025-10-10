#!/usr/bin/env python3
"""
Standalone test for MacroOptimizer (no bot dependencies)
"""

import sys
import json
from pathlib import Path

# Direct import without going through __init__
sys.path.insert(0, str(Path(__file__).parent / "shopping_bot"))

# Import only macro_optimizer (no bot_core)
import macro_optimizer


def test_macro_optimizer():
    """Test MacroOptimizer standalone"""
    print("=" * 80)
    print("TEST 1: MacroOptimizer - User specifies protein constraint")
    print("=" * 80)
    
    optimizer = macro_optimizer.get_macro_optimizer()
    
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


def test_nutrient_normalization():
    """Test nutrient name normalization"""
    print("=" * 80)
    print("TEST 4: Nutrient Name Normalization")
    print("=" * 80)
    
    optimizer = macro_optimizer.get_macro_optimizer()
    
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
    
    print("\n✅ Test 4 PASSED\n")


def test_category_profile_lookup():
    """Test category profile lookup"""
    print("=" * 80)
    print("TEST 5: Category Profile Lookup")
    print("=" * 80)
    
    optimizer = macro_optimizer.get_macro_optimizer()
    
    # Test chips profile
    profile = optimizer.get_category_profile("f_and_b/food/light_bites/chips_and_crisps")
    print(f"\nChips profile:")
    print(f"  - Has optimize rules: {'optimize' in profile}")
    print(f"  - Maximize targets: {len(profile.get('optimize', {}).get('maximize', []))}")
    print(f"  - Minimize targets: {len(profile.get('optimize', {}).get('minimize', []))}")
    
    assert 'optimize' in profile, "Should have optimize rules"
    assert len(profile['optimize']['minimize']) > 0, "Should have minimize targets"
    
    # Test energy bars profile
    profile2 = optimizer.get_category_profile("f_and_b/food/light_bites/energy_bars")
    print(f"\nEnergy bars profile:")
    print(f"  - Maximize targets: {len(profile2.get('optimize', {}).get('maximize', []))}")
    print(f"  - Minimize targets: {len(profile2.get('optimize', {}).get('minimize', []))}")
    
    assert 'optimize' in profile2, "Should have optimize rules"
    
    print("\n✅ Test 5 PASSED\n")


def main():
    """Run all tests"""
    print("\n" + "=" * 80)
    print("MACRO OPTIMIZER STANDALONE TESTS")
    print("=" * 80 + "\n")
    
    try:
        test_macro_optimizer()
        test_nutrient_normalization()
        test_category_profile_lookup()
        
        print("\n" + "=" * 80)
        print("✅ ALL TESTS PASSED!")
        print("=" * 80 + "\n")
        
        print("MacroOptimizer is working correctly!")
        print("\nKey behaviors verified:")
        print("  ✅ User constraints become hard filters")
        print("  ✅ Category defaults only apply when user has constraints")
        print("  ✅ No constraints = no macro filtering (use percentile ranking)")
        print("  ✅ Nutrient name normalization works")
        print("  ✅ Category profile lookup works")
        
        print("\nNext: Test full ES query generation with real bot")
        
    except Exception as e:
        print(f"\n❌ TEST FAILED: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()

