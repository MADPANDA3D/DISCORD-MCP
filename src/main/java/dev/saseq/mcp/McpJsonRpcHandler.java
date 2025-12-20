package dev.saseq.mcp;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

import java.util.ArrayList;
import java.util.List;

@Component
public class McpJsonRpcHandler {
    private static final Logger logger = LoggerFactory.getLogger(McpJsonRpcHandler.class);
    private static final String JSONRPC_VERSION = "2.0";
    private static final String MCP_PROTOCOL_VERSION = "2024-11-05";

    private final ObjectMapper objectMapper;
    private final McpToolRegistry toolRegistry;
    private final String serverName;
    private final String serverVersion;

    public McpJsonRpcHandler(ObjectMapper objectMapper,
                             McpToolRegistry toolRegistry,
                             @Value("${spring.ai.mcp.server.name:discord-mcp-server}") String serverName,
                             @Value("${spring.ai.mcp.server.version:0.0.1}") String serverVersion) {
        this.objectMapper = objectMapper;
        this.toolRegistry = toolRegistry;
        this.serverName = serverName;
        this.serverVersion = serverVersion;
    }

    public List<ObjectNode> handlePayload(String payload) {
        List<ObjectNode> responses = new ArrayList<>();
        try {
            JsonNode root = objectMapper.readTree(payload);
            if (root.isArray()) {
                for (JsonNode node : root) {
                    ObjectNode response = handleSingle(node);
                    if (response != null) {
                        responses.add(response);
                    }
                }
            } else {
                ObjectNode response = handleSingle(root);
                if (response != null) {
                    responses.add(response);
                }
            }
        } catch (Exception ex) {
            logger.warn("Failed to parse MCP request", ex);
            responses.add(errorResponse(null, -32700, "Parse error", ex.getMessage()));
        }
        return responses;
    }

    private ObjectNode handleSingle(JsonNode request) {
        if (request == null || request.isNull()) {
            return errorResponse(null, -32600, "Invalid Request", "Request payload is empty");
        }

        JsonNode idNode = request.get("id");
        String method = request.path("method").asText(null);
        if (method == null || method.isBlank()) {
            return errorResponse(idNode, -32600, "Invalid Request", "Missing method");
        }

        ObjectNode response = objectMapper.createObjectNode();
        response.put("jsonrpc", JSONRPC_VERSION);
        if (idNode != null) {
            response.set("id", idNode);
        } else {
            response.putNull("id");
        }

        try {
            switch (method) {
                case "initialize" -> response.set("result", buildInitializeResult());
                case "tools/list" -> response.set("result", buildToolsList());
                case "tools/call" -> response.set("result", handleToolCall(request.path("params")));
                case "resources/list" -> response.set("result", buildResourcesList());
                default -> {
                    return errorResponse(idNode, -32601, "Method not found", method);
                }
            }
        } catch (IllegalArgumentException ex) {
            return errorResponse(idNode, -32602, "Invalid params", ex.getMessage());
        } catch (Exception ex) {
            logger.warn("Failed to handle MCP method {}", method, ex);
            return errorResponse(idNode, -32603, "Internal error", ex.getMessage());
        }

        if (idNode == null || idNode.isNull()) {
            return null;
        }
        return response;
    }

    private ObjectNode buildInitializeResult() {
        ObjectNode result = objectMapper.createObjectNode();
        result.put("protocolVersion", MCP_PROTOCOL_VERSION);
        ObjectNode serverInfo = result.putObject("serverInfo");
        serverInfo.put("name", serverName);
        serverInfo.put("version", serverVersion);

        ObjectNode capabilities = result.putObject("capabilities");
        capabilities.putObject("tools").put("listChanged", false);
        capabilities.putObject("resources").put("listChanged", false);

        return result;
    }

    private ObjectNode buildToolsList() {
        ObjectNode result = objectMapper.createObjectNode();
        ArrayNode toolsArray = result.putArray("tools");
        for (ObjectNode toolNode : toolRegistry.listTools()) {
            toolsArray.add(toolNode);
        }
        return result;
    }

    private ObjectNode handleToolCall(JsonNode params) {
        if (params == null || params.isMissingNode() || params.isNull()) {
            throw new IllegalArgumentException("Missing params for tools/call");
        }
        String toolName = params.path("name").asText(null);
        if (toolName == null || toolName.isBlank()) {
            throw new IllegalArgumentException("Missing tool name");
        }

        JsonNode arguments = params.get("arguments");
        if (arguments == null || arguments.isNull()) {
            arguments = params.get("args");
        }

        String resultText = toolRegistry.callTool(toolName, arguments);
        ObjectNode result = objectMapper.createObjectNode();
        ArrayNode content = result.putArray("content");
        ObjectNode item = content.addObject();
        item.put("type", "text");
        item.put("text", resultText);
        return result;
    }

    private ObjectNode buildResourcesList() {
        ObjectNode result = objectMapper.createObjectNode();
        result.putArray("resources");
        return result;
    }

    private ObjectNode errorResponse(JsonNode idNode, int code, String message, String data) {
        ObjectNode response = objectMapper.createObjectNode();
        response.put("jsonrpc", JSONRPC_VERSION);
        if (idNode != null) {
            response.set("id", idNode);
        } else {
            response.putNull("id");
        }
        ObjectNode error = response.putObject("error");
        error.put("code", code);
        error.put("message", message);
        if (data != null && !data.isBlank()) {
            error.put("data", data);
        }
        return response;
    }
}
