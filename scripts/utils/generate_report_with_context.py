"""Generate a report with full document context and questions."""

import sys
import json
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from utils.data_loader import load_data_file
from utils.question_generator import generate_questions

def generate_report():
    print("="*80)
    print("QAGRedo - Question Generation Report with Context")
    print("="*80)
    print()
    
    # Load document
    print("Loading document...")
    docs = load_data_file("dev-data.jsonl")
    doc = docs[0]
    print(f"[OK] Loaded document: {doc['id']} - {doc['title']}")
    print()
    
    # Generate questions
    print("Generating questions with OpenAI API...")
    print("(This may take 15-30 seconds...)")
    print()
    
    results = generate_questions([doc])
    
    if not results or len(results) == 0:
        print("❌ No results generated")
        return
    
    result = results[0]
    
    # Generate report
    report = []
    report.append("="*80)
    report.append("QUESTION GENERATION REPORT")
    report.append("="*80)
    report.append("")
    report.append(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    report.append(f"Model: {result.get('generation_metadata', {}).get('model', 'N/A')}")
    report.append(f"Provider: {result.get('generation_metadata', {}).get('provider', 'N/A')}")
    report.append("")
    report.append("="*80)
    report.append("DOCUMENT INFORMATION")
    report.append("="*80)
    report.append("")
    report.append(f"Document ID: {result.get('id', 'N/A')}")
    report.append(f"Title: {result.get('title', 'N/A')}")
    report.append(f"Source: {result.get('source', 'N/A')}")
    report.append(f"Type: {result.get('type', 'N/A')}")
    report.append("")
    report.append("="*80)
    report.append("DOCUMENT CONTENT (FULL TEXT)")
    report.append("="*80)
    report.append("")
    
    # Get original document content
    original_doc = doc.get('content', '')
    if original_doc:
        # Format content with line breaks for readability
        content_lines = original_doc.split('\n')
        for line in content_lines:
            report.append(line)
    else:
        report.append("(No content found in document)")
    
    report.append("")
    report.append("="*80)
    report.append("GENERATED QUESTIONS")
    report.append("="*80)
    report.append("")
    
    questions = result.get('questions', [])
    if questions:
        for i, question in enumerate(questions, 1):
            report.append(f"Question {i}:")
            report.append(f"  {question}")
            report.append("")
    else:
        report.append("(No questions generated)")
    
    report.append("="*80)
    report.append("GENERATION METADATA")
    report.append("="*80)
    report.append("")
    
    metadata = result.get('generation_metadata', {})
    for key, value in metadata.items():
        report.append(f"  {key}: {value}")
    
    report.append("")
    report.append("="*80)
    report.append("END OF REPORT")
    report.append("="*80)
    
    # Print to console
    report_text = "\n".join(report)
    print(report_text)
    
    # Save to file
    output_file = "question_generation_report.txt"
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(report_text)
    
    print()
    print(f"[OK] Report saved to: {output_file}")
    
    # Also save JSON with full context
    json_output = {
        "document": {
            "id": doc.get('id'),
            "title": doc.get('title'),
            "source": doc.get('source'),
            "type": doc.get('type'),
            "content": doc.get('content')
        },
        "generated_questions": result.get('questions', []),
        "generation_metadata": result.get('generation_metadata', {})
    }
    
    json_file = "question_generation_report.json"
    with open(json_file, "w", encoding="utf-8") as f:
        json.dump(json_output, f, indent=2, ensure_ascii=False)
    
    print(f"[OK] JSON report saved to: {json_file}")

if __name__ == "__main__":
    generate_report()

