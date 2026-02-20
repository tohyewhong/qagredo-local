"""Grade Q&A results for hallucination."""

import sys
import json
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from utils import load_results, grade_qa_results, print_grading_report, save_results

# Force output flushing
sys.stdout.reconfigure(line_buffering=True)


def main():
    print("=" * 80)
    print("Q&A Hallucination Grading Tool")
    print("=" * 80)
    print()
    
    # Try to find the most recent Q&A results file
    output_dir = Path("output")
    qa_files = list(output_dir.rglob("*qa_results*.json"))
    
    if not qa_files:
        print("❌ No Q&A results files found in output/ directory")
        print()
        print("Usage:")
        print("  python grade_qa_results.py <path_to_qa_results.json>")
        print()
        print("Or run the script with a file path:")
        print("  python grade_qa_results.py output/openai/gpt-4/2025-11-17/qa_results_2docs.json")
        return
    
    # Use the most recent file if no argument provided
    if len(sys.argv) > 1:
        file_path = Path(sys.argv[1])
    else:
        # Sort by modification time, get most recent
        qa_files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        file_path = qa_files[0]
        print(f"Using most recent file: {file_path}")
        print()
    
    if not file_path.exists():
        print(f"❌ File not found: {file_path}")
        return
    
    print(f"Loading Q&A results from: {file_path}")
    print()
    
    try:
        # Load JSON file directly
        with open(file_path, 'r', encoding='utf-8') as f:
            qa_results = json.load(f)
        
        if not isinstance(qa_results, list):
            print("❌ Invalid file format. Expected a list of Q&A results.")
            return
        
        print(f"[OK] Loaded {len(qa_results)} Q&A results")
        print()
        
        # Grade the results
        print("Grading for hallucination...")
        print("(This may take a moment for semantic analysis...)")
        print()
        
        # Use semantic method if available, otherwise keyword
        graded_results = grade_qa_results(qa_results, method="semantic")
        
        print("[OK] Grading complete")
        print()
        
        # Print report
        print_grading_report(graded_results)
        
        # Save graded results
        output_path = file_path.parent / f"{file_path.stem}_graded{file_path.suffix}"
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(graded_results, f, indent=2, ensure_ascii=False)
        
        print(f"[OK] Saved graded results to: {output_path}")
        print()
        
        # Summary statistics
        print("=" * 80)
        print("SUMMARY STATISTICS")
        print("=" * 80)
        
        grades = [r.get('overall_grade', 'F') for r in graded_results]
        confidences = [r.get('overall_confidence', 0.0) for r in graded_results]
        
        grade_counts = {}
        for grade in ['A', 'B', 'C', 'D', 'F']:
            grade_counts[grade] = grades.count(grade)
        
        print(f"Total Documents: {len(graded_results)}")
        print(f"Average Confidence: {sum(confidences) / len(confidences):.1%}")
        print()
        print("Grade Distribution:")
        for grade in ['A', 'B', 'C', 'D', 'F']:
            count = grade_counts[grade]
            percentage = (count / len(graded_results)) * 100 if graded_results else 0
            bar = "█" * int(percentage / 2)
            print(f"  {grade}: {count:2d} ({percentage:5.1f}%) {bar}")
        print()
        
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()

