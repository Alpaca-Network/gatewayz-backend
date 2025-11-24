/**
 * Type definitions for Gatewayz HuggingFace client
 */

export enum TaskType {
  TEXT_GENERATION = "text-generation",
  CONVERSATIONAL = "conversational",
  SUMMARIZATION = "summarization",
  TRANSLATION = "translation",
  QUESTION_ANSWERING = "question-answering",
  TEXT_CLASSIFICATION = "text-classification",
  TOKEN_CLASSIFICATION = "token-classification",
  IMAGE_GENERATION = "image-generation",
  EMBEDDING = "embedding",
}

export interface TextGenerationRequest {
  inputs: string;
  parameters?: Record<string, unknown>;
  model?: string;
}

export interface TextGenerationOutput {
  generated_text: string;
}

export interface TextGenerationResponse {
  output: TextGenerationOutput[];
}

export interface ConversationalMessage {
  role: "user" | "assistant";
  content: string;
}

export interface ConversationalRequest {
  past_user_inputs?: string[];
  generated_responses?: string[];
  text: string;
}

export interface ConversationalResponse {
  conversation: {
    past_user_inputs: string[];
    generated_responses: string[];
  };
  warnings?: string[];
}

export interface SummarizationRequest {
  inputs: string;
  parameters?: Record<string, unknown>;
}

export interface SummarizationOutput {
  summary_text: string;
}

export interface SummarizationResponse {
  output: SummarizationOutput;
}

export interface TranslationRequest {
  inputs: string;
  target_language?: string;
}

export interface TranslationOutput {
  translation_text: string;
}

export interface QuestionAnsweringRequest {
  question: string;
  context: string;
}

export interface QuestionAnsweringOutput {
  answer: string;
  score?: number;
}

export interface ModelInfo {
  model_id: string;
  hub_model_id: string;
  task_type: string;
  provider?: string;
  parameters?: Record<string, unknown>;
}

export interface CostInfo {
  task: string;
  model: string;
  input_tokens: number;
  output_tokens: number;
  cost_nano_usd: number;
  cost_usd?: number;
}

export interface BillingInfo {
  total_cost_nano_usd: number;
  costs: CostInfo[];
  currency: string;
}

export interface UsageRecord {
  request_id: string;
  timestamp: string;
  task: string;
  model: string;
  input_tokens: number;
  output_tokens: number;
  cost_nano_usd: number;
}

export interface UsageResponse {
  records: UsageRecord[];
  total_records: number;
  total_cost_nano_usd: number;
  total_cost_usd?: number;
}

export interface ClientOptions {
  apiKey: string;
  baseUrl?: string;
  timeout?: number;
}

export interface RequestOptions {
  timeout?: number;
  headers?: Record<string, string>;
}
