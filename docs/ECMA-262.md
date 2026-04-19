# ECMA-262 Function Naming Guide

This document maps every Changes Worker transform function to its **ECMAScript (ECMA-262)** equivalent. The goal is to use standard JavaScript/ECMAScript names wherever possible so users don't have to re-learn a new vocabulary.

> **ECMAScript** is the official name of the JavaScript standard (ECMA-262).
> See: [W3Schools JavaScript Reference](https://www.w3schools.com/js/default.asp)

---

## Naming Principles

When adding or renaming a function, follow these rules in priority order:

1. **Use the exact ECMAScript method name** if one exists (e.g., `trim`, `slice`, `replace`, `split`, `join`, `concat`, `indexOf`, `includes`, `startsWith`, `endsWith`, `keys`, `values`, `flat`).
2. **Use the `to` + Type convention** for type conversions — this mirrors ECMAScript's `toString()`, `parseInt()`, `parseFloat()`, `Number()` patterns. Keep the snake_case style for multi-word names (e.g., `to_string`, `to_int`).
3. **Use the `Math.*` name** for math functions (e.g., `round`, `ceil`, `floor`, `abs`) — these are already standard.
4. **Use a verb + noun pattern** for operations that have no ECMAScript counterpart (e.g., `format_date`, `parse_date`). Prefer snake_case for readability in mapping JSON.
5. **Never invent a new name** when a standard one exists.

---

## Current Functions → ECMA-262 Mapping

### String Functions

| Current Name | ECMA-262 Equivalent | Action | Notes |
|---|---|---|---|
| `trim()` | [`String.prototype.trim()`](https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Global_Objects/String/trim) | ✅ Keep | Already standard |
| `ltrim()` | [`String.prototype.trimStart()`](https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Global_Objects/String/trimStart) | ⚠️ **Rename → `trimStart()`** | `ltrim` is a PHP/Python convention. ES2019 added `trimStart()`. `trimLeft()` is a legacy alias. Prefer `trimStart()`. |
| `rtrim()` | [`String.prototype.trimEnd()`](https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Global_Objects/String/trimEnd) | ⚠️ **Rename → `trimEnd()`** | Same reasoning. ES2019 added `trimEnd()`. `trimRight()` is a legacy alias. Prefer `trimEnd()`. |
| `uppercase()` | [`String.prototype.toUpperCase()`](https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Global_Objects/String/toUpperCase) | ⚠️ **Rename → `toUpperCase()`** | ECMAScript uses `toUpperCase()`. |
| `lowercase()` | [`String.prototype.toLowerCase()`](https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Global_Objects/String/toLowerCase) | ⚠️ **Rename → `toLowerCase()`** | ECMAScript uses `toLowerCase()`. |
| `camelcase()` | _(no built-in)_ | ✅ Keep | No ES equivalent. Convention is clear. Could consider naming `toCamelCase()` to match the `to*` pattern but `camelcase()` is already widely understood. |
| `propercase()` | _(no built-in)_ | ⚠️ **Rename → `toTitleCase()`** | The web/typography standard term is "Title Case". No ES built-in, but `toTitleCase()` follows the `to*` pattern and is universally understood. "Proper case" is a Microsoft Office term. |
| `concat(,sep)` | [`String.prototype.concat()`](https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Global_Objects/String/concat) / [`Array.prototype.join()`](https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Global_Objects/Array/join) | ✅ Keep | `concat` is standard. Your version adds a separator which is more like `join`, but the name is fine for combining fields. |
| `replace(,old,new)` | [`String.prototype.replace()`](https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Global_Objects/String/replace) | ✅ Keep | Already standard. Your version replaces all occurrences, which maps to ES2021 `replaceAll()`. Consider aliasing `replaceAll` too. |
| `replace_regex(,pat,repl)` | [`String.prototype.replace()`](https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Global_Objects/String/replace) with RegExp | ✅ Keep | No direct rename needed. In ES, `replace(/regex/g, repl)` handles this. The explicit name `replace_regex` is clearer for a config-driven system. |
| `strip_chars(,chars)` | _(no built-in)_ | ✅ Keep | No ES equivalent. Python-inspired. Name is descriptive. |
| `pad_left(,len,char)` | [`String.prototype.padStart()`](https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Global_Objects/String/padStart) | ⚠️ **Rename → `padStart()`** | ES2017 added `padStart()`. |
| `pad_right(,len,char)` | [`String.prototype.padEnd()`](https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Global_Objects/String/padEnd) | ⚠️ **Rename → `padEnd()`** | ES2017 added `padEnd()`. |
| `substr(,start,len)` | [`String.prototype.substring()`](https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Global_Objects/String/substring) / [`String.prototype.slice()`](https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Global_Objects/String/slice) | ⚠️ **Rename → `substring()`** | `substr()` is deprecated in ES. The standard methods are `substring(start, end)` and `slice(start, end)`. Since you already have `slice()` for arrays, keep `substring()` for strings. Note: you already have `substring()` implemented in mapper.py but expose it as `substr()` in the UI. |
| `split(,sep)[i]` | [`String.prototype.split()`](https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Global_Objects/String/split) | ✅ Keep | Already standard |
| `join(,sep)` | [`Array.prototype.join()`](https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Global_Objects/Array/join) | ✅ Keep | Already standard |
| `length()` | [`String.prototype.length`](https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Global_Objects/String/length) | ✅ Keep | In ES it's a property, not a method, but `length()` as a function is the right call for a transform system. |
| `urlencode()` | [`encodeURIComponent()`](https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Global_Objects/encodeURIComponent) | ⚠️ **Rename → `encodeURIComponent()`** | Standard ES global function. `urlencode` is a PHP name. |
| `urldecode()` | [`decodeURIComponent()`](https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Global_Objects/decodeURIComponent) | ⚠️ **Rename → `decodeURIComponent()`** | Standard ES global function. `urldecode` is a PHP name. |

### Numeric / Type Conversion

| Current Name | ECMA-262 Equivalent | Action | Notes |
|---|---|---|---|
| `to_int()` | [`parseInt()`](https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Global_Objects/parseInt) | ⚠️ **Rename → `parseInt()`** | Standard ES global function. |
| `to_float()` | [`parseFloat()`](https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Global_Objects/parseFloat) | ⚠️ **Rename → `parseFloat()`** | Standard ES global function. |
| `to_decimal(,n)` | [`Number.prototype.toFixed()`](https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Global_Objects/Number/toFixed) | ⚠️ **Rename → `toFixed()`** | `toFixed(n)` returns a string with `n` decimal places. Your `to_decimal` returns a Decimal — which is better for RDBMS. Keep the numeric behavior but use the ES name. |
| `to_string()` | [`*.toString()`](https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Global_Objects/Object/toString) | ⚠️ **Rename → `toString()`** | Standard ES method on all objects. Drop the underscore. |
| `round()` | [`Math.round()`](https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Global_Objects/Math/round) | ✅ Keep | Already matches `Math.round()` |
| `ceil()` | [`Math.ceil()`](https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Global_Objects/Math/ceil) | ✅ Keep | Already matches `Math.ceil()` |
| `floor()` | [`Math.floor()`](https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Global_Objects/Math/floor) | ✅ Keep | Already matches `Math.floor()` |
| `abs()` | [`Math.abs()`](https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Global_Objects/Math/abs) | ✅ Keep | Already matches `Math.abs()` |
| `coalesce(,default)` | [Nullish coalescing `??`](https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Operators/Nullish_coalescing) | ✅ Keep | In ES it's the `??` operator, not a function. `coalesce` is the SQL/universal name and works well as a function. |

### Date / Time

| Current Name | ECMA-262 Equivalent | Action | Notes |
|---|---|---|---|
| `to_date()` | [`new Date()`](https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Global_Objects/Date/Date) | ✅ Keep | No single ES equivalent function name. `to_date()` is clear. |
| `to_iso8601()` | [`Date.prototype.toISOString()`](https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Global_Objects/Date/toISOString) | ⚠️ **Rename → `toISOString()`** | Standard ES method. |
| `to_epoch()` | [`Date.prototype.getTime()`](https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Global_Objects/Date/getTime) | ⚠️ **Consider → `getTime()`** | `getTime()` returns milliseconds in ES. Your version returns seconds. Keep `to_epoch()` if you want seconds, or rename to `getTime()` with a note that it returns seconds (not ms). Either is defensible. |
| `from_epoch()` | [`new Date(ms)`](https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Global_Objects/Date/Date) | ✅ Keep | No direct ES function name. `from_epoch()` is clear. |
| `format_date(,fmt)` | [`Intl.DateTimeFormat`](https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Global_Objects/Intl/DateTimeFormat) | ✅ Keep | ES uses `Intl.DateTimeFormat` which is locale-based, not format-string-based. `format_date` is the universally understood name. |
| `parse_date(,fmt)` | [`Date.parse()`](https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Global_Objects/Date/parse) | ✅ Keep | ES `Date.parse()` doesn't accept format strings. `parse_date` is clearer. |
| `date_add(,unit,n)` | _(no built-in)_ | ✅ Keep | No ES equivalent. SQL-inspired. Universal name. |
| `date_diff(,unit)` | _(no built-in)_ | ✅ Keep | No ES equivalent. SQL-inspired. Universal name. |
| `truncate_date(,unit)` | _(no built-in)_ | ✅ Keep | No ES equivalent. The name is descriptive. |
| `now()` | [`Date.now()`](https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Global_Objects/Date/now) | ✅ Keep | Already matches `Date.now()`. |

### Array / Object

| Current Name | ECMA-262 Equivalent | Action | Notes |
|---|---|---|---|
| `flatten()` | [`Array.prototype.flat()`](https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Global_Objects/Array/flat) | ⚠️ **Rename → `flat()`** | ES2019 added `Array.prototype.flat()`. `flatten` was the proposal name but `flat()` is the standard. |
| `slice(,start,end)` | [`Array.prototype.slice()`](https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Global_Objects/Array/slice) | ✅ Keep | Already standard |
| `keys()` | [`Object.keys()`](https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Global_Objects/Object/keys) | ✅ Keep | Already standard |
| `values()` | [`Object.values()`](https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Global_Objects/Object/values) | ✅ Keep | Already standard |

### Encoding / Hash

| Current Name | ECMA-262 Equivalent | Action | Notes |
|---|---|---|---|
| `json_safe()` | _(no built-in)_ | ✅ Keep | Custom function. Name is descriptive. |
| `json_parse()` | [`JSON.parse()`](https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Global_Objects/JSON/parse) | ✅ Keep | Matches the ES name (just with underscore for snake_case consistency). |
| `json_stringify()` | [`JSON.stringify()`](https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Global_Objects/JSON/stringify) | ✅ Keep | Matches the ES name. |
| `base64_encode()` | [`btoa()`](https://developer.mozilla.org/en-US/docs/Web/API/btoa) | ✅ Keep | `btoa()`/`atob()` are Web API names, not ECMA-262. `base64_encode`/`base64_decode` are far more readable and self-documenting. |
| `base64_decode()` | [`atob()`](https://developer.mozilla.org/en-US/docs/Web/API/atob) | ✅ Keep | Same as above. Keep the readable name. |
| `md5()` | _(no built-in)_ | ✅ Keep | Not in ES. Name is the algorithm name — correct. |
| `sha256()` | _(no built-in)_ | ✅ Keep | Not in ES. Name is the algorithm name — correct. |
| `uuid()` | [`crypto.randomUUID()`](https://developer.mozilla.org/en-US/docs/Web/API/Crypto/randomUUID) | ✅ Keep | `crypto.randomUUID()` is a Web API, not ECMA-262. `uuid()` is shorter and universally understood. |

### Conditional

| Current Name | ECMA-262 Equivalent | Action | Notes |
|---|---|---|---|
| `if(,op,then,else)` | [Conditional (ternary) `?:`](https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Operators/Conditional_operator) | ✅ Keep | In ES it's the `? :` ternary operator. `if()` is the universal programming term and works well as a function. |

### Functions in `mapper.py` Not in the UI

These functions exist in the Python backend (`schema/mapper.py`) but are not listed in the glossary or the UI dropdown:

| Current Name | ECMA-262 Equivalent | Action | Notes |
|---|---|---|---|
| `left(,n)` | _(no built-in)_ | ⚠️ **Remove / Replace** | Use `substring(,0,n)` or `slice(,0,n)` instead. `left()` is a SQL/VB convention, not ES. |
| `right(,n)` | _(no built-in)_ | ⚠️ **Remove / Replace** | Use `slice(,-n)` instead. `right()` is a SQL/VB convention, not ES. |
| `substring(,s,len)` | [`String.prototype.substring()`](https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Global_Objects/String/substring) | ✅ Keep | Already standard. This is what `substr` in the UI should point to. |
| `startswith(,prefix)` | [`String.prototype.startsWith()`](https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Global_Objects/String/startsWith) | ⚠️ **Rename → `startsWith()`** | ES6 added `startsWith()` (camelCase, capital W). |
| `endswith(,suffix)` | [`String.prototype.endsWith()`](https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Global_Objects/String/endsWith) | ⚠️ **Rename → `endsWith()`** | ES6 added `endsWith()` (camelCase, capital W). |
| `contains(,substr)` | [`String.prototype.includes()`](https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Global_Objects/String/includes) | ⚠️ **Rename → `includes()`** | ES6 added `includes()`. `contains` was the original proposal name but was changed to `includes`. |
| `regex_match(,pat)` | [`RegExp.prototype.test()`](https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Global_Objects/RegExp/test) | ⚠️ **Consider → `test()`** | ES uses `regex.test(string)`. However, `regex_match` is more self-documenting for a config-driven system. Either name is defensible. |

---

## Summary of Recommended Renames

| # | Current Name | New Name (ECMA-262) | ES Reference |
|---|---|---|---|
| 1 | `ltrim()` | `trimStart()` | `String.prototype.trimStart()` |
| 2 | `rtrim()` | `trimEnd()` | `String.prototype.trimEnd()` |
| 3 | `uppercase()` | `toUpperCase()` | `String.prototype.toUpperCase()` |
| 4 | `lowercase()` | `toLowerCase()` | `String.prototype.toLowerCase()` |
| 5 | `propercase()` | `toTitleCase()` | _(convention, follows `to*` pattern)_ |
| 6 | `pad_left()` | `padStart()` | `String.prototype.padStart()` |
| 7 | `pad_right()` | `padEnd()` | `String.prototype.padEnd()` |
| 8 | `substr()` | `substring()` | `String.prototype.substring()` |
| 9 | `urlencode()` | `encodeURIComponent()` | `encodeURIComponent()` |
| 10 | `urldecode()` | `decodeURIComponent()` | `decodeURIComponent()` |
| 11 | `to_int()` | `parseInt()` | `parseInt()` |
| 12 | `to_float()` | `parseFloat()` | `parseFloat()` |
| 13 | `to_decimal()` | `toFixed()` | `Number.prototype.toFixed()` |
| 14 | `to_string()` | `toString()` | `Object.prototype.toString()` |
| 15 | `to_iso8601()` | `toISOString()` | `Date.prototype.toISOString()` |
| 16 | `flatten()` | `flat()` | `Array.prototype.flat()` |
| 17 | `startswith()` | `startsWith()` | `String.prototype.startsWith()` |
| 18 | `endswith()` | `endsWith()` | `String.prototype.endsWith()` |
| 19 | `contains()` | `includes()` | `String.prototype.includes()` |
| 20 | `to_epoch()` | `getTime()` _(optional)_ | `Date.prototype.getTime()` |

---

## Functions That Are Already Standard — No Change Needed

`trim`, `concat`, `replace`, `split`, `join`, `length`, `round`, `ceil`, `floor`, `abs`, `coalesce`, `slice`, `keys`, `values`, `now`, `json_parse`, `json_stringify`, `base64_encode`, `base64_decode`, `md5`, `sha256`, `uuid`, `if`, `json_safe`, `replace_regex`, `strip_chars`, `to_date`, `from_epoch`, `format_date`, `parse_date`, `date_add`, `date_diff`, `truncate_date`

---

## Adding New Functions — Decision Checklist

When you need to add a new transform function:

1. **Does ECMAScript have a built-in with this name?**
   - Yes → Use the exact ES name (camelCase). Examples: `at()`, `indexOf()`, `findIndex()`, `entries()`, `every()`, `some()`, `map()`, `filter()`, `reduce()`, `reverse()`, `sort()`, `fill()`, `find()`, `from()`, `isArray()`, `isNaN()`, `isFinite()`, `repeat()`, `matchAll()`, `normalize()`, `codePointAt()`, `charCodeAt()`, `charAt()`.
   - No → Go to step 2.

2. **Does `Math.*` have it?**
   - Yes → Use the `Math.*` name (e.g., `min`, `max`, `pow`, `sqrt`, `log`, `trunc`, `sign`, `clz32`, `hypot`).
   - No → Go to step 3.

3. **Is it a type conversion?**
   - Yes → Use the `to*()` or `parse*()` pattern (e.g., `toBoolean()`, `toJSON()`, `parseInt()`, `parseFloat()`).
   - No → Go to step 4.

4. **Is it a date operation?**
   - Yes → Check the [Date prototype](https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Global_Objects/Date) first (e.g., `getFullYear()`, `getMonth()`, `getDate()`, `getHours()`, `toLocaleDateString()`). If no match, use `date_*` prefix with snake_case (e.g., `date_add`, `date_diff`).
   - No → Go to step 5.

5. **Is it a well-known name from SQL or another standard?**
   - Yes → Use it (e.g., `coalesce`, `nullif`, `greatest`, `least`, `cast`).
   - No → Use a descriptive verb+noun in snake_case (e.g., `strip_chars`, `json_safe`).

### Quick Reference: Candidate Future Functions

| Potential Function | ECMA-262 Name | Category |
|---|---|---|
| Check if value is a number | `isNaN()` / `isFinite()` | Numeric |
| Repeat a string N times | `repeat(,n)` | String |
| Get character at index | `charAt(,i)` | String |
| Find index of substring | `indexOf(,str)` | String |
| Check if array has value | `includes(,val)` | Array |
| Reverse a string/array | `reverse()` | Array/String |
| Get array element at index | `at(,i)` | Array |
| Truncate number (no rounding) | `trunc()` | Numeric (`Math.trunc`) |
| Min of two values | `min(,val)` | Numeric (`Math.min`) |
| Max of two values | `max(,val)` | Numeric (`Math.max`) |
| Power | `pow(,exp)` | Numeric (`Math.pow`) |
| Square root | `sqrt()` | Numeric (`Math.sqrt`) |
| Sign (+1, -1, 0) | `sign()` | Numeric (`Math.sign`) |
| Get year from date | `getFullYear()` | Date |
| Get month from date | `getMonth()` | Date |
| Get day from date | `getDate()` | Date |
| Boolean conversion | `toBoolean()` | Type (follows `to*` pattern) |
| Locale-formatted date | `toLocaleDateString(,locale)` | Date |

---

## Migration Strategy

When renaming functions:

1. **Add the new name** as an alias in `mapper.py` `apply_transform()` — both old and new names work.
2. **Update the UI** (`TRANSFORMS` array in `schema.html`, glossary.html) to show the new name.
3. **Keep the old name working** in `mapper.py` for backward compatibility with existing mapping JSON files.
4. **Log a deprecation warning** when the old name is used, pointing to the new name.
5. **Update docs** (this file, `SCHEMA_MAPPING.md`, `ADMIN_UI.md`) to reference the new names.
6. **After N releases**, remove the old name aliases.

---

## References

- [ECMA-262 Specification](https://tc39.es/ecma262/)
- [MDN JavaScript Reference](https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference)
- [W3Schools JavaScript Tutorial](https://www.w3schools.com/js/default.asp)
- [ECMAScript Compatibility Table](https://compat-table.github.io/compat-table/es6/)
