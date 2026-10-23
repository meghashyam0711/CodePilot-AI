# Stage V: Class and Method AI-DOCS Extraction Fix Report

**Project:** CodePilot AI v0.4.0  
**Project Root:** `D:\PROJECTS\CodePilot AI`  
**Date:** October 23, 2026  
**Phase:** Stage V — Class and Method AI-DOCS Extraction Fix  
**Status:** Completed & Fully Verified  
**Automated Tests:** **369 / 369 Tests Passing** (100% Pass Rate across 25 Modules)  

---

## 1. Executive Summary

In Stage U (`reports/stage_u_second_file_validation.md`), CodePilot AI evaluated documentation extraction on a class method (`ScoreNormalizer.normalize_scores` from `S:\VOICE\SRISHTI_SHIKSHA\ai_engine\doc_retrieval\ranking\score_normalizer.py`). The local Qwen 1.5B model re-emitted the outer `class ScoreNormalizer:` definition and method signature. The Stage S extractor evaluated `len(tree.body) == 1` and took `tree.body[0].body` (the class body containing `FunctionDef`), expecting an immediate `ast.Expr` docstring. It did not traverse down into the inner method's body, resulting in `(False, "", False)` and halting with `FixError`.

Stage V implemented a comprehensive fix in `src/codepilot/core/fixer.py` that enables CodePilot to safely resolve and extract documentation from model completions containing classes, nested methods, and asynchronous methods, while strictly preserving executable code invariance.

### Key Outcomes:
1. **Hierarchical Symbol Traversal:** Implemented `_collect_named_nodes()` in `extract_documentation_content()` to recursively discover all `FunctionDef`, `AsyncFunctionDef`, and `ClassDef` nodes with their hierarchical qualified names (e.g. `ScoreNormalizer.normalize_scores`, `Outer.Inner.compute`).
2. **Deterministic Target Resolution:** 
   - Supports exact qualified name matches (`Class.method`).
   - Supports unambiguous simple name matches (`normalize_scores`) when a model wraps a single target method inside its class.
   - Rejects mis-targeted outputs (where model outputs a different symbol than requested).
   - Rejects ambiguous outputs (where multiple methods share the same simple name without qualification).
3. **Empty & Malformed Output Rejection:** Added explicit validation that rejects empty docstrings (`""""""`), whitespace-only docstrings, or code completions lacking docstrings.
4. **Multiline Signature Awareness:** Enhanced `apply_documentation_replacement()` with parenthesis-depth tracking so docstrings placed above multiline function or method declarations are correctly inserted below the closing colon `):` of the header.
5. **Enclosing Class Fallback:** Enhanced `find_enclosing_function()` to fall back to enclosing `ClassDef` when an instruction is located at the class level outside of any method.
6. **Deterministic Unit Testing:** Added 8 new deterministic unit tests in `tests/test_fix.py` covering all required scenarios without running real-model inference.
7. **Regression Stability:** Focused tests expanded from 26 to **34 passing tests** (0.72s). Full test suite expanded from 361 to **369 passing tests** (10.39s across 25 modules).

---

## 2. Root Cause Analysis of the Stage U Limitation

In Stage S, `extract_documentation_content()` implemented:
```python
    else:
        if len(tree.body) == 1 and isinstance(
            tree.body[0], (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
        ):
            target_body = tree.body[0].body
        elif target_symbol:
            for b in tree.body:
                if (
                    isinstance(b, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                    and b.name == target_symbol
                ):
                    target_body = b.body
                    break
```

When given:
```python
class ScoreNormalizer:
    def normalize_scores(self, results, score_key):
        """Docstring here."""
        if not results:
            return results
```
1. `tree.body[0]` was `ClassDef("ScoreNormalizer")`.
2. `len(tree.body) == 1` matched `ClassDef`, assigning `target_body = tree.body[0].body`.
3. `target_body` was the *class* body: `[FunctionDef("normalize_scores")]`.
4. `first_stmt = target_body[0]` was a `FunctionDef`, not an `ast.Expr` string docstring.
5. `elif target_symbol:` was bypassed. Even if evaluated, it only inspected module-level nodes (`tree.body`), never descending into `ClassDef.body`.
6. Result: `(False, "", False)` returned, raising `FixError`.

---

## 3. Implementation Details

### 3.1 Hierarchical AST Symbol Collector
In `src/codepilot/core/fixer.py`:
```python
    def _collect_named_nodes(parent_node: ast.AST, parent_path: str = "") -> List[Tuple[ast.AST, str, str]]:
        collected = []
        for child in getattr(parent_node, "body", []):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                qname = f"{parent_path}.{child.name}" if parent_path else child.name
                collected.append((child, qname, child.name))
                collected.extend(_collect_named_nodes(child, qname))
        return collected
```

### 3.2 Target Matching Hierarchy
```python
        named_nodes = _collect_named_nodes(tree)
        target_sym = target_symbol.strip() if target_symbol else None

        if target_sym:
            # 1. Exact qualified match (e.g. "ScoreNormalizer.normalize_scores")
            exact = [n for n, q, s in named_nodes if q == target_sym]
            if len(exact) == 1:
                target_body = exact[0].body
            else:
                # 2. Suffix / simple name match (e.g. "normalize_scores")
                matches = [
                    n for n, q, s in named_nodes
                    if s == target_sym
                    or q.endswith("." + target_sym)
                    or ("." in target_sym and target_sym.endswith("." + s) and s == target_sym.split(".")[-1])
                ]
                if len(matches) == 1:
                    target_body = matches[0].body
                elif len(matches) > 1:
                    # Ambiguous target symbol matching multiple definitions
                    return False, "", False
                elif named_nodes:
                    # Named nodes exist in tree, but none matched target_sym -> wrong target
                    return False, "", False
                else:
                    # No named definitional nodes in tree (pure statements/docstring)
                    target_body = tree.body
        else:
            if len(named_nodes) == 1:
                target_body = named_nodes[0][0].body
            elif len(named_nodes) > 1:
                # Multiple definitional candidates without a target symbol to disambiguate
                return False, "", False
            else:
                target_body = tree.body
```

### 3.3 Strict Empty Docstring Rejection
```python
    first_stmt = target_body[0]
    if (
        isinstance(first_stmt, ast.Expr)
        and isinstance(first_stmt.value, ast.Constant)
    ):
        raw_val = first_stmt.value.value
        if not isinstance(raw_val, str) or not raw_val.strip():
            # Reject empty or whitespace-only docstrings
            return False, "", False

        start_idx = first_stmt.lineno - 1 - offset_line
        end_idx = getattr(first_stmt, "end_lineno", first_stmt.lineno) - offset_line
        if 0 <= start_idx < len(lines):
            doc_lines = lines[start_idx:end_idx]
            extracted_doc = textwrap.dedent("".join(doc_lines)).strip()
            if not extracted_doc:
                return False, "", False
            return True, extracted_doc, True
```

### 3.4 Multiline Signature Header Traversal
In `apply_documentation_replacement()`:
```python
        # Advance insert_idx past multiline function/class signatures to the closing colon
        insert_idx = def_idx + 1
        paren_depth = 0
        header_ended = False
        for cur_i in range(def_idx, len(new_lines)):
            line_str = new_lines[cur_i]
            for ch in line_str:
                if ch in "([":
                    paren_depth += 1
                elif ch in ")]":
                    paren_depth = max(0, paren_depth - 1)
            if paren_depth == 0 and ":" in line_str:
                insert_idx = cur_i + 1
                header_ended = True
                break
        if not header_ended:
            insert_idx = def_idx + 1
```

---

## 4. Test Suite and Verification

8 new deterministic unit tests were added to `tests/test_fix.py`:

| Test Name | Purpose | Verification Status |
|---|---|:---:|
| `test_ai_docs_stage_v_standalone_function_preserved` | Verifies Stage T behavior for standalone functions with docstring and duplicated body | **PASSED** |
| `test_ai_docs_stage_v_class_method_with_duplicated_body` | Verifies extraction when model re-emits class definition, method header, and body (Stage U fix) | **PASSED** |
| `test_ai_docs_stage_v_class_multiple_methods_selection` | Verifies correct method selection in class with multiple methods (`add`, `subtract`) | **PASSED** |
| `test_ai_docs_stage_v_nested_class_method` | Verifies method targeting inside nested classes (`Outer.Inner.compute`) | **PASSED** |
| `test_ai_docs_stage_v_async_method` | Verifies docstring extraction on `async def` method | **PASSED** |
| `test_ai_docs_stage_v_wrong_or_ambiguous_target_rejected` | Verifies rejection when model targets a different symbol or ambiguous candidates | **PASSED** |
| `test_ai_docs_stage_v_malformed_and_empty_output_rejected` | Verifies direct rejection of empty docstrings (`""""""`), whitespace, and code without docstrings | **PASSED** |
| `test_ai_docs_stage_v_patch_rejection_and_rollback` | Verifies end-to-end AST invariance rejection (`PatchError`) and byte-for-byte rollback | **PASSED** |

### Execution Commands and Test Output:

#### 1. Focused Tests:
```powershell
python -m pytest -o pythonpath=src tests/test_fix.py
```
```
============================= 34 passed in 0.72s ==============================
```

#### 2. Full Regression Suite:
```powershell
python -m pytest -o pythonpath=src tests/
```
```
============================ 369 passed in 10.39s =============================
```

---

## 5. Summary of Compliance with Constraints

- **Zero Real-Model Inference:** Real-model inference was strictly **not** executed during this implementation phase.
- **External Project Untouched:** `S:\VOICE\SRISHTI_SHIKSHA` was not modified.
- **Historical Reports Preserved:** Reports from v1–v5 and Stages R, S, T, and U remain unmodified.
- **No Refactoring Outside Scope:** Changes were confined strictly to `extract_documentation_content`, `apply_documentation_replacement`, and `find_enclosing_function` in `src/codepilot/core/fixer.py`, plus new tests in `tests/test_fix.py`.
- **AST Invariance Preserved:** All AST invariance checks (`verify_executable_ast_invariance`) and pre-write rollback protections remain active and verified.

---

## 6. Stop Condition & Next Step Readiness

Stage V implementation is complete and verified with deterministic tests. The system is ready for authorization to proceed to Stage W (re-evaluating real-model execution on `score_normalizer.py`).
