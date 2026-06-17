GRAPH_FIELD_SEP = "<SEP>"

PROMPTS = {}

PROMPTS["DEFAULT_TUPLE_DELIMITER"] = "<|>"
PROMPTS["DEFAULT_RECORD_DELIMITER"] = "##"
PROMPTS["DEFAULT_COMPLETION_DELIMITER"] = "<|COMPLETE|>"
PROMPTS["process_tickers"] = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

PROMPTS["DEFAULT_ENTITY_TYPES"] = ["organization", "person", "location", "event"]


PROMPTS["entity_extraction"] = """-目标-
给定一个可能与该活动相关的文本文档和实体类型列表，从文本中识别所有这些类型的实体以及已识别实体之间的所有关系。

-步骤-
1. 识别所有实体。对于每个已识别的实体，提取以下信息：
- entity_name：实体名称，使用与输入文本相同的语言。如果是英文，请将名称首字母大写。
- entity_type：以下类型之一：[{entity_types}]
- entity_description：对实体属性和活动的全面描述
将每个实体格式化为 ("entity"{tuple_delimiter}<entity_name>{tuple_delimiter}<entity_type>{tuple_delimiter}<entity_description>)

2. 从步骤1中识别的实体中，识别所有彼此*明显相关*的（source_entity，target_entity）对。
对于每对相关实体，提取以下信息：
- source_entity：源实体名称，如步骤1中所识别
- target_entity：目标实体名称，如步骤1中所识别
- relationship_description：解释为什么您认为源实体和目标实体彼此相关
- relationship_strength：表示源实体和目标实体之间关系强度的数字分数
- relationship_keywords：一个或多个高级关键词，用于概括关系的总体性质，侧重于概念或主题而非具体细节
将每个关系格式化为 ("relationship"{tuple_delimiter}<source_entity>{tuple_delimiter}<target_entity>{tuple_delimiter}<relationship_description>{tuple_delimiter}<relationship_keywords>{tuple_delimiter}<relationship_strength>)

3. 识别概括整个文本的主要概念、主题或话题的高级关键词。这些应该捕捉文档中存在的总体思想。
将内容级别关键词格式化为 ("content_keywords"{tuple_delimiter}<high_level_keywords>)

4. 用中文返回输出，作为步骤1和2中识别的所有实体和关系的单个列表。使用 **{record_delimiter}** 作为列表分隔符。

5. 完成后，输出 {completion_delimiter}

######################
-Examples-
{examples}
######################
-Real Data-
######################
Entity_types: {entity_types}
Text: {input_text}
######################
Output:
"""


PROMPTS[
    "entiti_continue_extraction"
] = """**有很多**实体在上次提取中被遗漏。请使用相同格式在下面添加它们：
"""

PROMPTS[
    "entiti_if_loop_extraction"
] = """似乎仍然遗漏了一些实体。如果仍有需要添加的实体，请回答 YES | NO。
"""

PROMPTS["fail_response"] = "抱歉，我无法为该问题提供答案。"
