package dev.saseq.mcp;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.aop.support.AopUtils;
import org.springframework.core.annotation.AnnotationUtils;
import org.springframework.stereotype.Component;
import org.springframework.stereotype.Service;
import org.springframework.util.ReflectionUtils;
import org.springframework.context.ApplicationContext;
import org.springframework.ai.tool.annotation.Tool;
import org.springframework.ai.tool.annotation.ToolParam;

import jakarta.annotation.PostConstruct;
import java.lang.reflect.Method;
import java.lang.reflect.Parameter;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

@Component
public class McpToolRegistry {
    private static final Logger logger = LoggerFactory.getLogger(McpToolRegistry.class);

    private final ApplicationContext applicationContext;
    private final ObjectMapper objectMapper;
    private final Map<String, ToolMethod> tools = new ConcurrentHashMap<>();

    public McpToolRegistry(ApplicationContext applicationContext, ObjectMapper objectMapper) {
        this.applicationContext = applicationContext;
        this.objectMapper = objectMapper;
    }

    @PostConstruct
    public void initialize() {
        Map<String, Object> serviceBeans = applicationContext.getBeansWithAnnotation(Service.class);
        for (Object bean : serviceBeans.values()) {
            Class<?> targetClass = AopUtils.getTargetClass(bean);
            for (Method method : targetClass.getMethods()) {
                Tool tool = AnnotationUtils.findAnnotation(method, Tool.class);
                if (tool == null) {
                    continue;
                }
                ToolMethod toolMethod = new ToolMethod(bean, method, tool);
                tools.put(toolMethod.name, toolMethod);
                logger.debug("Registered MCP tool {}", toolMethod.name);
            }
        }
    }

    public List<ObjectNode> listTools() {
        if (tools.isEmpty()) {
            return Collections.emptyList();
        }

        List<ObjectNode> toolNodes = new ArrayList<>();
        for (ToolMethod toolMethod : tools.values()) {
            toolNodes.add(toolMethod.toDefinition(objectMapper));
        }
        return toolNodes;
    }

    public String callTool(String name, JsonNode arguments) {
        ToolMethod toolMethod = tools.get(name);
        if (toolMethod == null) {
            throw new IllegalArgumentException("Unknown tool: " + name);
        }
        return toolMethod.invoke(arguments, objectMapper);
    }

    private static class ToolMethod {
        private final Object target;
        private final Method method;
        private final String name;
        private final String description;
        private final List<ToolParamDescriptor> params;

        ToolMethod(Object target, Method method, Tool tool) {
            this.target = target;
            this.method = method;
            this.name = (tool.name() == null || tool.name().isBlank()) ? method.getName() : tool.name();
            this.description = tool.description();
            this.params = describeParameters(method);
        }

        ObjectNode toDefinition(ObjectMapper mapper) {
            ObjectNode definition = mapper.createObjectNode();
            definition.put("name", name);
            definition.put("description", description == null ? "" : description);

            ObjectNode schema = mapper.createObjectNode();
            schema.put("type", "object");
            ObjectNode properties = schema.putObject("properties");
            ArrayNode required = schema.putArray("required");

            for (ToolParamDescriptor param : params) {
                ObjectNode paramSchema = properties.putObject(param.name());
                paramSchema.put("type", param.jsonType());
                if (param.description() != null && !param.description().isBlank()) {
                    paramSchema.put("description", param.description());
                }
                if (param.required()) {
                    required.add(param.name());
                }
            }

            if (required.isEmpty()) {
                schema.remove("required");
            }

            definition.set("inputSchema", schema);
            return definition;
        }

        String invoke(JsonNode arguments, ObjectMapper mapper) {
            Object[] args = new Object[params.size()];
            for (int i = 0; i < params.size(); i++) {
                ToolParamDescriptor param = params.get(i);
                JsonNode valueNode = arguments == null ? null : arguments.get(param.name());

                if (valueNode == null || valueNode.isNull()) {
                    if (param.required()) {
                        throw new IllegalArgumentException("Missing required parameter: " + param.name());
                    }
                    args[i] = param.defaultValue();
                    continue;
                }

                args[i] = param.convertValue(valueNode, mapper);
            }

            try {
                ReflectionUtils.makeAccessible(method);
                Object result = method.invoke(target, args);
                return result == null ? "" : String.valueOf(result);
            } catch (Exception ex) {
                throw new IllegalStateException("Failed to invoke tool " + name, ex);
            }
        }

        private static List<ToolParamDescriptor> describeParameters(Method method) {
            Parameter[] parameters = method.getParameters();
            List<ToolParamDescriptor> descriptors = new ArrayList<>();
            for (Parameter parameter : parameters) {
                ToolParam toolParam = parameter.getAnnotation(ToolParam.class);
                String description = null;
                boolean required = true;
                String name = null;

                if (toolParam != null) {
                    description = toolParam.description();
                    Object nameValue = AnnotationUtils.getValue(toolParam, "name");
                    if (nameValue instanceof String nameString && !nameString.isBlank()) {
                        name = nameString;
                    } else {
                        Object value = AnnotationUtils.getValue(toolParam, "value");
                        if (value instanceof String valueString && !valueString.isBlank()) {
                            name = valueString;
                        }
                    }
                    Object requiredValue = AnnotationUtils.getValue(toolParam, "required");
                    if (requiredValue instanceof Boolean requiredBool) {
                        required = requiredBool;
                    }
                }

                if (name == null || name.isBlank()) {
                    name = parameter.getName();
                }

                descriptors.add(new ToolParamDescriptor(name, description, required, parameter.getType()));
            }
            return descriptors;
        }
    }

    private record ToolParamDescriptor(String name, String description, boolean required, Class<?> type) {
        String jsonType() {
            if (String.class.equals(type)) {
                return "string";
            }
            if (Boolean.class.equals(type) || boolean.class.equals(type)) {
                return "boolean";
            }
            if (Integer.class.equals(type) || int.class.equals(type)
                    || Long.class.equals(type) || long.class.equals(type)
                    || Short.class.equals(type) || short.class.equals(type)
                    || Byte.class.equals(type) || byte.class.equals(type)) {
                return "integer";
            }
            if (Double.class.equals(type) || double.class.equals(type)
                    || Float.class.equals(type) || float.class.equals(type)) {
                return "number";
            }
            return "string";
        }

        Object convertValue(JsonNode valueNode, ObjectMapper mapper) {
            if (String.class.equals(type)) {
                return valueNode.asText();
            }
            return mapper.convertValue(valueNode, type);
        }

        Object defaultValue() {
            if (!type.isPrimitive()) {
                return null;
            }
            if (boolean.class.equals(type)) {
                return false;
            }
            if (int.class.equals(type)) {
                return 0;
            }
            if (long.class.equals(type)) {
                return 0L;
            }
            if (short.class.equals(type)) {
                return (short) 0;
            }
            if (byte.class.equals(type)) {
                return (byte) 0;
            }
            if (double.class.equals(type)) {
                return 0d;
            }
            if (float.class.equals(type)) {
                return 0f;
            }
            return null;
        }
    }
}
