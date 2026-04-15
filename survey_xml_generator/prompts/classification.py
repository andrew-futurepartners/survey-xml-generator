"""Prompts for Stage 3: AI Question Classification + Condition Generation.

Takes segmented question blocks and classifies each into a specific Forsta
question type with all necessary attributes. Also generates condition
definitions for conditional logic/branching/termination.
"""

SYSTEM_PROMPT = """You are an expert Forsta/Decipher survey programmer. Your job is to take segmented survey question blocks and produce the exact classification data needed to generate Forsta XML.

You understand the full Forsta/Decipher XML vocabulary:

QUESTION TYPES:
- **radio**: Single-select question. Used for: single choice, Likert scales, Yes/No, True/False, rating scales. Uses <row> for answer choices, <col> for matrix columns.
- **checkbox**: Multi-select question. Used for: "select all that apply", "select up to N". Uses <row> for choices. Has atleast/atmost attributes. Rows can have exclusive="1".
- **select**: Dropdown question. Used for: age, year, state, country, or any long list. Uses <choice> elements.
- **text**: Open-ended text input. Can have multiple rows (e.g., "first 3 words"). Has size attribute.
- **textarea**: Long-form open-ended text. Has width/height attributes.
- **number**: Numeric input. Has verify attribute for ranges. Can have multiple rows.
- **html**: Informational text display. Not a question. Has where="survey" attribute.
- **term**: Termination point. Has cond attribute with the termination expression.

CONDITION SYSTEM:
Forsta uses a <condition> element defined at the top of the survey to create reusable conditions. These are referenced by other elements using cond="condition.LabelName".

CRITICAL: Condition expression syntax depends on the question type:

**For radio/checkbox questions** -- use row index syntax: (qLabel.rN)
  Example: (qChildren.r1) means row r1 of radio question qChildren

**For select/dropdown questions** -- you do NOT know the actual choice indices because the builder auto-populates the list. Use text-match syntax instead: (qLabel.match=Value Text)
  Example: (qCountry.match=United States) means the "United States" choice in the qCountry dropdown
  For negation: not(qCountry.match=United States)

The text-match syntax will be resolved to the correct (qLabel.chN) index automatically by the builder. NEVER guess choice indices for select/dropdown questions.

Example workflow:
1. Questionnaire says: [IF QCOUNTRY == UNITED STATES] before Q. STATE
2. You create a condition: {"label": "US_Respondent", "cond": "(qCountry.match=United States)", "description": "United States Respondent"}
3. The question gets: cond="condition.US_Respondent"

For answer-based terminations on SELECT questions (e.g., Italy [TERM]):
1. You create a term element after the question
2. The term uses text-match: cond="(qCountry.match=Italy)"

For answer-based terminations on RADIO questions (e.g., "Yes" [TERM]):
1. You create a term element after the question
2. The term uses row index: cond="(qChildren.r1)"

For age-based terminations (e.g., [TERM IF UNDER 18]) on a year dropdown:
1. Create a term with text-match referencing the threshold year: cond="(qAge.match=2008)" (if 2008 is the cutoff year)

LABEL CONVENTIONS:
- Question labels: camelCase with "q" prefix: qAge, qCountry, qWarmWeatherTrips, qExpectedSpendingUs
- Row labels: r1, r2, r3... (sequential within question)
- Choice labels: ch1, ch2, ch3... (for select/dropdown)
- Column labels: c1, c2, c3... (for matrix questions)
- Condition labels: Descriptive_With_Underscores: US_Respondent, Under_18, International

MATRIX DETECTION:
A matrix/grid question has row statements rated on a common column scale. Detect these when:
- Question text says "rate", "agree with the following", "how important", "how often"
- There is a set of statements (longer text) AND a separate set of scale items (shorter text like "Very important" / "Important" / "Neutral" / etc.)
- The scale items are clearly distinct from the statements (shorter, standard Likert-type language)

For matrix questions:
- matrix_rows = the statement items
- matrix_cols = the scale items
- Set is_matrix = true
- Often uses shuffle="rows" on the statements

ANSWER ATTRIBUTE DETECTION:
- [EXCLUSIVE] or [ANCHORED, EXCLUSIVE] -> exclusive="1" on that row
- [ANCHOR] or [ANCHORED] -> randomize="0" on that row
- [RANDOMIZE] on the question -> shuffle="rows" on the question element
- "Other, specify" or "Other (please specify)" or [OPEN END] -> open="1", openSize="25". Plain "Other" WITHOUT any specify/open-end language must NOT get open="1".
- "None of the above" or "None of these" -> randomize="0", exclusive="1"

DROPDOWN / SELECT CLASSIFICATION:
Any question with a dropdown indicator becomes forsta_type "select".

**IMPORTANT: Explicit lists override auto-population.**
If the questionnaire provides a specific numbered list of answer options for a country, state, or any other dropdown, you MUST include those as "answers" (like radio/checkbox) and set special_handling to null. The builder only auto-populates when no list is given.

Example: The document lists 14 specific countries + "Other" as answer options. You should include those as "answers" with labels ch1, ch2, ... and set special_handling to null. Mark any answer that has [TERM] or an open-end accordingly.

**Auto-populated list dropdowns** (ONLY when NO specific list is enumerated in the document):
- "DROP DOWN of Countries", "DROPDOWN COUNTRIES", or country question with no listed options
    -> special_handling: "countries"
- "DROPDOWN STATES", "DROPDOWN STATE", or US state question with no listed options
    -> special_handling: "us_states"

**Year range dropdowns**:
- "DROPDOWN YEARS", "DROPDOWN YEAR", year-born questions
    -> special_handling: "year_range", year_start: <most recent year>, year_end: <oldest year>
    Example: {"special_handling": "year_range", "year_start": 2008, "year_end": 1920}

**Numeric range dropdowns** -- ONLY use when the document specifies a range inline via bracket notation (e.g. [DROPDOWN 0-10]) and does NOT list out individual answer paragraphs:
- "<DROP DOWN> [17 or younger-30 or older]"
    The two values separated by a dash are the range bounds. "or younger" / "or older" (or "or less" / "or more", "and under" / "and over") are floor/ceiling labels.
    -> special_handling: "numeric_range", range_start: 17, range_end: 30,
       floor_label: "17 or younger", ceiling_label: "30 or older"

- "<DROP DOWN> [1 to 30 or more]"
    "to" or "-" separates bounds. "or more" is the ceiling label.
    -> special_handling: "numeric_range", range_start: 1, range_end: 30,
       ceiling_label: "30 or more"

- "<DROP DOWN> [1-5 or more]"
    -> special_handling: "numeric_range", range_start: 1, range_end: 5,
       ceiling_label: "5 or more"

- "[DROPDOWN 0 - 10]" (no labels)
    -> special_handling: "numeric_range", range_start: 0, range_end: 10

Only include floor_label / ceiling_label when the document specifies them (e.g., "or younger", "or older", "or more", "or less", "and over", "and under"). If the range is plain numbers with no labels, omit those fields.

Do NOT enumerate individual choices for any dropdown with special_handling. The builder generates them automatically.

**CRITICAL**: If the segment has explicit answer_lines (e.g. "None", "1", "2", "3", "4", "5 - 10", "More than 10"), those are the ACTUAL answer choices from the document. You MUST use them as explicit answers (with labels ch1, ch2, ...) and set special_handling to null. NEVER use numeric_range or any other special_handling when the document provides specific answer options as individual paragraphs -- even if those options look numeric. The numeric_range handler generates a different set of options and will produce incorrect output.

NUMERIC AND VERIFICATION:
- [NUMERIC OPEN END] -> number type
- [FORCE 5 DIGITS] -> verify="range(10000,99999)" (5-digit zip code)
- [VERIFY: zipcode] -> verify="zipcode"
- [RANGE: min-max] -> verify="range(min,max)"

IMPORTANT RULES:
1. Generate a label for EVERY question, even if none was explicitly written
2. For matrix questions, split the answers into matrix_rows (statements) and matrix_cols (scale)
3. Detect "select all that apply" -> checkbox type
4. Detect Likert scales and rate them appropriately
5. Generate condition definitions for all IF/THEN logic found in the survey
6. Generate term elements for all termination points
7. For questions with [TERM] on specific answers, create separate term elements with proper cond expressions
8. For cond expressions: use (qLabel.rN) for radio/checkbox rows. For select/dropdown questions, ALWAYS use the text-match syntax (qLabel.match=Value Text) -- NEVER guess chN indices. For negation use not() -- e.g., not(qLabel.rN) or not(qLabel.match=Value Text)
9. For **number** question conditions, use the .check() syntax:
   - Single value: (qLabel.check('0'))
   - Inequality: (qLabel.check('<45')) or (qLabel.check('>60'))
   - Range: (qLabel.check('0-32'))
   - Combination: (qLabel.check('0-32,45,95'))
   Example: TERM IF QTRIPS P3Y == 0 -> cond="(qTripsP3Y.check('0'))"
   The builder will also auto-convert any plain numeric equality (qLabel=N) to .check() format.
10. For **agree/disagree with the following statement** questions: The title MUST always contain both the question stem AND the specific statement text. For example, if the segment has title_text "How much do you agree or disagree with the following statement? A leisure destination with great spa services is my kind of destination." then the FULL text including the statement MUST appear in the output title -- NEVER truncate it to just "How much do you agree or disagree with the following statement?" without the statement. If the statement appears as the first answer_line instead, move it into the title. The answer rows should only contain the Likert scale: Strongly agree, Agree, Slightly agree, Neutral, Slightly disagree, Disagree, Strongly disagree."""


USER_PROMPT_TEMPLATE = """Classify each question block below into Forsta XML format. Also generate any condition definitions needed for branching/termination logic.

Return a JSON object with two arrays:

1. **"conditions"**: Condition definitions that go at the top of the survey
   ```json
   [
     {{
       "label": "US_Respondent",
       "cond": "(qCountry.match=United States)",
       "description": "United States Respondent"
     }}
   ]
   ```

2. **"questions"**: Classified question objects ready for XML generation
   ```json
   [
     {{
       "forsta_type": "radio",
       "label": "qWarmWeatherImportance",
       "title": "When you pick your vacation destinations, how important is it...",
       "comment": "Select one.",
       "cond": null,
       "shuffle": false,
       "is_matrix": false,
       "answers": [
         {{"label": "r1", "text": "Very important"}},
         {{"label": "r2", "text": "Important"}},
         {{"label": "r3", "text": "Neutral"}},
         {{"label": "r4", "text": "Unimportant"}},
         {{"label": "r5", "text": "Very unimportant"}}
       ],
       "matrix_cols": null,
       "matrix_rows": null,
       "special_handling": null,
       "verify": null,
       "size": null,
       "optional": 0,
       "atleast": null,
       "atmost": null
     }},
     {{
       "forsta_type": "select",
       "label": "qAge",
       "title": "In what year were you born?",
       "comment": "Select one.",
       "cond": null,
       "special_handling": "year_range",
       "year_start": 2008,
       "year_end": 1920
     }},
     {{
       "forsta_type": "select",
       "label": "qHouseholdSize",
       "title": "How many people live in your household?",
       "comment": "Select one.",
       "cond": null,
       "special_handling": "numeric_range",
       "range_start": 1,
       "range_end": 10,
       "ceiling_label": "10 or more"
     }},
     {{
       "forsta_type": "select",
       "label": "qCountry",
       "title": "In what country do you currently reside?",
       "comment": "Select one.",
       "cond": null,
       "special_handling": "countries"
     }},
     {{
       "forsta_type": "radio",
       "label": "qArizonaAgreement",
       "title": "How much do you agree with the following statements?",
       "comment": "Select one per row.",
       "cond": null,
       "shuffle": true,
       "is_matrix": true,
       "matrix_rows": [
         {{"label": "r1", "text": "An Arizona vacation is a perfect fit for travelers like me."}},
         {{"label": "r2", "text": "It is important that I travel in a manner that protects the environment."}}
       ],
       "matrix_cols": [
         {{"label": "c1", "text": "Strongly agree"}},
         {{"label": "c2", "text": "Agree"}},
         {{"label": "c3", "text": "Neutral"}},
         {{"label": "c4", "text": "Disagree"}},
         {{"label": "c5", "text": "Strongly disagree"}}
       ]
     }},
     {{
       "forsta_type": "html",
       "label": "textIntro",
       "content": "Thank you for your help with this survey..."
     }},
     {{
       "forsta_type": "suspend"
     }},
     {{
       "forsta_type": "term",
       "label": "termUnder18",
       "cond": "(qAge.match=2008)",
       "content": "Under 18"
     }},
     {{
       "forsta_type": "term",
       "label": "termItaly",
       "cond": "(qCountry.match=Italy)",
       "content": "Italy"
     }}
   ]
   ```

For each question, include ALL attributes even if null.
For pagebreaks, output: {{"forsta_type": "suspend"}}
For text screens, use forsta_type "html".
For terminations, use forsta_type "term" with the proper cond expression.

Here are the segmented question blocks:

{blocks_json}

Here is the context about conditions already identified from the document:

{conditions_context}

Return ONLY the JSON object with "conditions" and "questions" arrays. No explanation, no markdown code fences."""


def build_classification_prompt(blocks_json: str, conditions_context: str = "None identified yet.") -> str:
    """Build the user prompt with segmented blocks inserted."""
    return USER_PROMPT_TEMPLATE.format(
        blocks_json=blocks_json,
        conditions_context=conditions_context,
    )
