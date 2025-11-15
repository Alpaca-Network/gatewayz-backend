/**
 * Gatewayz Client for HuggingFace Task API
 *
 * Provides TypeScript/JavaScript client for accessing Gatewayz models
 * through HuggingFace's inference provider network.
 *
 * Example:
 *   const client = new GatewayzClient({ apiKey: "..." });
 *   const response = await client.textGeneration("Hello, world!");
 */

import fetch from "node-fetch";
import {
  TextGenerationRequest,
  TextGenerationResponse,
  ConversationalRequest,
  ConversationalResponse,
  SummarizationRequest,
  SummarizationResponse,
  TranslationRequest,
  TranslationOutput,
  QuestionAnsweringRequest,
  QuestionAnsweringOutput,
  ModelInfo,
  BillingInfo,
  UsageResponse,
  ClientOptions,
  RequestOptions,
} from "./types";

export class GatewayzClient {
  private apiKey: string;
  private baseUrl: string;
  private timeout: number;
  private headers: Record<string, string>;

  /**
   * Initialize client
   *
   * @param options Client configuration options
   */
  constructor(options: ClientOptions) {
    this.apiKey = options.apiKey;
    this.baseUrl = (options.baseUrl || "https://gatewayz.io").replace(/\/$/, "");
    this.timeout = options.timeout || 60000;
    this.headers = {
      Authorization: `Bearer ${this.apiKey}`,
      "Content-Type": "application/json",
      "User-Agent": "gatewayz-js-hf/0.1.0",
    };
  }

  /**
   * Build full URL from path
   */
  private buildUrl(path: string): string {
    const cleanPath = path.startsWith("/") ? path : `/${path}`;
    return `${this.baseUrl}${cleanPath}`;
  }

  /**
   * Make HTTP request
   */
  private async request<T>(
    method: string,
    path: string,
    data?: unknown,
    options?: RequestOptions
  ): Promise<T> {
    const url = this.buildUrl(path);
    const headers = { ...this.headers, ...options?.headers };
    const timeout = options?.timeout || this.timeout;

    const config: RequestInit = {
      method,
      headers,
      timeout,
    };

    if (data) {
      config.body = JSON.stringify(data);
    }

    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), timeout);

    try {
      const response = await fetch(url, {
        ...config,
        signal: controller.signal,
      });

      if (!response.ok) {
        const errorText = await response.text();
        throw new Error(
          `API Error ${response.status}: ${errorText}`
        );
      }

      return (await response.json()) as T;
    } finally {
      clearTimeout(timeoutId);
    }
  }

  /**
   * Generate text given input prompt
   *
   * @param inputs Input text/prompt
   * @param model Model to use (defaults to gpt-3.5-turbo)
   * @param parameters Optional generation parameters
   * @returns Text generation response
   */
  async textGeneration(
    inputs: string,
    model?: string,
    parameters?: Record<string, unknown>
  ): Promise<TextGenerationResponse> {
    const request: TextGenerationRequest = {
      inputs,
      parameters: parameters || {},
      model,
    };

    return this.request<TextGenerationResponse>(
      "POST",
      "/hf/tasks/text-generation",
      request
    );
  }

  /**
   * Generate conversational response
   *
   * @param text Current user input
   * @param pastUserInputs Previous user inputs
   * @param generatedResponses Previous model responses
   * @returns Conversational response
   */
  async conversational(
    text: string,
    pastUserInputs?: string[],
    generatedResponses?: string[]
  ): Promise<ConversationalResponse> {
    const request: ConversationalRequest = {
      text,
      past_user_inputs: pastUserInputs || [],
      generated_responses: generatedResponses || [],
    };

    return this.request<ConversationalResponse>(
      "POST",
      "/hf/tasks/conversational",
      request
    );
  }

  /**
   * Summarize text
   *
   * @param inputs Text to summarize
   * @param parameters Optional parameters
   * @returns Summarization response
   */
  async summarization(
    inputs: string,
    parameters?: Record<string, unknown>
  ): Promise<SummarizationResponse> {
    const request: SummarizationRequest = {
      inputs,
      parameters,
    };

    return this.request<SummarizationResponse>(
      "POST",
      "/hf/tasks/summarization",
      request
    );
  }

  /**
   * Translate text
   *
   * @param inputs Text to translate
   * @param targetLanguage Target language
   * @returns Translation output
   */
  async translation(
    inputs: string,
    targetLanguage?: string
  ): Promise<TranslationOutput> {
    const request: TranslationRequest = {
      inputs,
      target_language: targetLanguage,
    };

    const response = await this.request<{ output: TranslationOutput }>(
      "POST",
      "/hf/tasks/translation",
      request
    );

    return response.output;
  }

  /**
   * Answer question based on context
   *
   * @param question Question to answer
   * @param context Context to answer from
   * @returns Question answering output
   */
  async questionAnswering(
    question: string,
    context: string
  ): Promise<QuestionAnsweringOutput> {
    const request: QuestionAnsweringRequest = {
      question,
      context,
    };

    const response = await this.request<{ output: QuestionAnsweringOutput }>(
      "POST",
      "/hf/tasks/question-answering",
      request
    );

    return response.output;
  }

  /**
   * List available models
   *
   * @param taskType Optional filter by task type
   * @returns List of available models
   */
  async listModels(taskType?: string): Promise<ModelInfo[]> {
    const params = new URLSearchParams();
    if (taskType) {
      params.append("task_type", taskType);
    }

    const url = `${this.buildUrl("/hf/tasks/models")}${
      params.toString() ? `?${params.toString()}` : ""
    }`;

    const response = await fetch(url, {
      method: "GET",
      headers: this.headers,
      timeout: this.timeout,
    });

    if (!response.ok) {
      throw new Error(`API Error ${response.status}`);
    }

    const data = (await response.json()) as {
      models: ModelInfo[];
    };
    return data.models;
  }

  /**
   * Calculate cost of requests
   *
   * @param requests List of request objects
   * @returns Billing information
   */
  async calculateCost(
    requests: Array<{
      task: string;
      model: string;
      input_tokens: number;
      output_tokens: number;
    }>
  ): Promise<BillingInfo> {
    return this.request<BillingInfo>(
      "POST",
      "/hf/tasks/billing/cost",
      { requests }
    );
  }

  /**
   * Get usage records for billing
   *
   * @param limit Max records to return
   * @param offset Offset for pagination
   * @returns Usage records
   */
  async getUsage(limit: number = 100, offset: number = 0): Promise<UsageResponse> {
    const params = new URLSearchParams({
      limit: limit.toString(),
      offset: offset.toString(),
    });

    const url = `${this.buildUrl(
      "/hf/tasks/billing/usage"
    )}?${params.toString()}`;

    const response = await fetch(url, {
      method: "GET",
      headers: this.headers,
      timeout: this.timeout,
    });

    if (!response.ok) {
      throw new Error(`API Error ${response.status}`);
    }

    return (await response.json()) as UsageResponse;
  }
}

/**
 * Create a new Gatewayz client
 *
 * @param apiKey API key for authentication
 * @param baseUrl Optional base URL (defaults to https://gatewayz.io)
 * @returns GatewayzClient instance
 */
export function createClient(
  apiKey: string,
  baseUrl?: string
): GatewayzClient {
  return new GatewayzClient({
    apiKey,
    baseUrl,
  });
}
