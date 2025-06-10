from update_all_stock_github import get_variants_bulk

if __name__ == "__main__":
    test_skus = ['122A-BLK-0']
    variants = get_variants_bulk(test_skus)
    print("Variants returned from Shopify:")
    print(variants) 