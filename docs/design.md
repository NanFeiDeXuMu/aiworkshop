# SmartLearn Agent - Product Design

## User Story

1. 作为一个学生, 我想要上传一个pdf并询问相关的问题, 从而让我的学习更加高效.
2. 作为一个学生, 我想要知道回答出自资料的哪一页, 从而让我快速复习相关知识.
3. 作为一个学生, 我希望让AI记住知识库, 并深入讨论相关话题.


## Feature List

| Priority | Feature | Day |
|----------|---------|-----|
| P0 | pdf 文本解析 | Day 2 |
| P0 | LLM 问答,基于page citations | Day 2 |
| P1 | RAG 管线 | Day 3 |
| P1 | web UI | Day 3 |
| P2 | 对话历史 | Day 3 |

## What We Will NOT Build

- 登录权限控制
- 多文件支持
- 移动应用


## Data Flow

### Day 2: Simple Mode

PDF File
  -> [pdf extractor]          # How do we get text out?
  -> pages[]
  -> [pages + questions]          # How do we combine with question?
  -> [LLM]
  -> Answer with [Page X]


### Day 3: RAG Mode

PDF -> [pdf extract] -> pages
    -> [split into chunks] -> chunks with source_page
    -> [embed] -> embeddings
    -> [vector store]  # storage

Question -> [encode] -> [similarity search] -> relevant chunks -> [LLM] -> Answer
