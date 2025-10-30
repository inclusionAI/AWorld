#!/usr/bin/env python3
"""
🔓 XBench Dataset Decryption Script
Compatible with Python 3.8+
"""

import base64
import csv
import sys
from pathlib import Path


def xor_decrypt(data: bytes, key: str) -> bytes:
    """
    XOR decrypt data with a key
    
    Args:
        data: Encrypted bytes data
        key: Decryption key string
        
    Returns:
        Decrypted bytes data
        
    Example:
        >>> encrypted = b"some_encrypted_data"
        >>> decrypted = xor_decrypt(encrypted, "my_key")
    """
    key_bytes = key.encode('utf-8')
    key_length = len(key_bytes)
    return bytes([data[i] ^ key_bytes[i % key_length] for i in range(len(data))])


def decrypt_csv_file(input_file: str, output_file: str) -> None:
    """
    Decrypt an XBench CSV file
    
    Args:
        input_file: Path to encrypted CSV file
        output_file: Path to save decrypted CSV file
        
    Example:
        >>> decrypt_csv_file("DeepSearch.csv", "DeepSearch_decrypted.csv")
    """
    input_path = Path(input_file)
    output_path = Path(output_file)
    
    if not input_path.exists():
        print(f"❌ Error: Input file not found: {input_file}")
        sys.exit(1)
    
    print(f"📄 Reading encrypted file: {input_path.name}")
    
    # Read and decrypt
    decrypted_rows = []
    with open(input_path, mode='r', encoding='utf-8-sig') as file:
        reader = csv.DictReader(file)
        fieldnames = reader.fieldnames
        
        for i, row in enumerate(reader, 1):
            try:
                # Get the decryption key from canary field
                key = row.get("canary", "")
                if not key:
                    print(f"⚠️  Warning: Row {i} has no canary key, skipping decryption")
                    decrypted_rows.append(row)
                    continue
                
                # Decrypt prompt and answer fields
                if "prompt" in row and row["prompt"]:
                    encrypted_prompt = base64.b64decode(row["prompt"])
                    row["prompt"] = xor_decrypt(encrypted_prompt, key).decode('utf-8')
                
                if "answer" in row and row["answer"]:
                    encrypted_answer = base64.b64decode(row["answer"])
                    row["answer"] = xor_decrypt(encrypted_answer, key).decode('utf-8')
                
                decrypted_rows.append(row)
                
                if i % 10 == 0:
                    print(f"   ✅ Processed {i} rows...", end='\r')
                    
            except Exception as e:
                print(f"\n❌ Error processing row {i}: {e}")
                sys.exit(1)
    
    print(f"\n   ✅ Total rows processed: {len(decrypted_rows)}")
    
    # Write decrypted data
    print(f"💾 Writing decrypted file: {output_path.name}")
    with open(output_path, mode='w', newline='', encoding='utf-8') as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(decrypted_rows)
    
    print(f"✅ Decryption completed successfully!")
    print(f"📁 Output file: {output_path.absolute()}")


def main():
    """Main entry point"""
    if len(sys.argv) < 2:
        print("Usage: python decrypt_xbench.py <input_csv> [output_csv]")
        print("\nExample:")
        print("  python decrypt_xbench.py DeepSearch.csv")
        print("  python decrypt_xbench.py DeepSearch.csv DeepSearch_decrypted.csv")
        sys.exit(1)
    
    input_file = sys.argv[1]
    output_file = sys.argv[2] if len(sys.argv) > 2 else input_file.replace('.csv', '_decrypted.csv')
    
    print("🚀 XBench Dataset Decryption Tool")
    print("=" * 50)
    
    decrypt_csv_file(input_file, output_file)
    
    print("\n⚠️  SECURITY NOTICE:")
    print("   - DO NOT upload decrypted data online")
    print("   - DO NOT commit to public repositories")
    print("   - Keep decrypted datasets local only")


if __name__ == "__main__":
    main()

