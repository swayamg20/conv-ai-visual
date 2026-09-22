"""
Agent prompt compilation and capability mapping.

Compiles structured persona data into actionable system prompts
and maps capability strings to tool names.
"""


def _is_physics_subject(subject: str) -> bool:
    """Return True when the subject string clearly points to physics tutoring."""
    normalized = subject.lower()
    physics_keywords = (
        "physics",
        "mechanics",
        "kinematics",
        "dynamics",
        "motion",
        "forces",
        "jee",
        "cbse",
    )
    return any(keyword in normalized for keyword in physics_keywords)


def compile_agent_prompt(persona: dict, capabilities: list[str]) -> str:
    """
    Compile a structured persona dict + capabilities into a full system prompt.

    Args:
        persona: Dict with keys like role, subject, level, goals, learning_style, language.
        capabilities: List of capability strings, e.g. ["canvas", "web_search"].

    Returns:
        A complete system prompt string ready for LLMPipeline.
    """
    role = persona.get("role", "student")
    subject = persona.get("subject", "general topics")
    level = persona.get("level", "")
    goals = persona.get("goals", "")
    learning_style = persona.get("learning_style", "step-by-step")
    language = persona.get("language", "English")

    # Build identity section
    level_clause = f" at the {level} level" if level else ""
    lines = [
        f"You are a personal AI tutor specializing in {subject}{level_clause}.",
        f"Your student's role is: {role}.",
    ]

    # Goals
    if goals:
        lines.append(f"The student's goals: {goals}.")
        lines.append(
            "Tailor every explanation toward these goals. Reference them when motivating concepts."
        )

    # Learning style
    style_instructions = {
        "visual": "Use diagrams, charts, and visual components whenever possible. Show before you tell.",
        "examples-first": "Always start with a concrete example before introducing theory or definitions.",
        "step-by-step": "Break every explanation into numbered steps. Never skip steps, even if they seem obvious.",
    }
    style_line = style_instructions.get(
        learning_style, f"Adapt your teaching to a {learning_style} learning style."
    )
    lines.append(f"Learning style preference: {style_line}")

    # Language
    if language and language.lower() != "english":
        lines.append(
            f"Communicate in {language}. Use technical terms in English but explain in the preferred language."
        )

    # Capability-specific instructions
    if "canvas" in capabilities:
        lines.append("")
        lines.append("CANVAS INSTRUCTIONS:")
        lines.append(
            "You have access to a visual whiteboard canvas. Use the teach_with_visuals tool for explanations."
        )
        lines.append(
            "Every visual explanation must use the teach_with_visuals tool with step-by-step narration."
        )
        lines.append(
            "Prefer visual components (diagrams, charts, equations) over long text narration."
        )
        lines.append(
            "When solving a problem, show the setup first, then the reasoning, then the final result."
        )
        lines.append(
            "Keep each visual step focused: one idea per step, with short voice narration."
        )
        lines.append(
            "Use labels, arrows, highlights, and equations to make the student's next step obvious."
        )

        if _is_physics_subject(subject):
            lines.append("For physics, be concrete and diagram-first.")
            lines.append(
                "For a same-speed projectile comparison launched and landed at the same height "
                "with no drag, use start_projectile_storyboard instead of teach_with_visuals."
            )
            lines.append(
                "If the learner omits values, use the verified 20 m/s and 30°/60° defaults. "
                "After starting it, acknowledge the visual handoff briefly instead of narrating "
                "a competing lesson."
            )
            lines.append("Use free-body diagrams for force problems before writing equations.")
            lines.append(
                "For inclined planes, draw the slope, angle, weight, normal, and resolved force components before solving."
            )
            lines.append(
                "For projectile motion, draw axes, launch angle, initial velocity components, and key points of the trajectory."
            )
            lines.append(
                "For graph questions, explicitly name the axes, units, slope, intercept, area, and what each means physically."
            )
            lines.append(
                "For function or motion plots, use coordinate_plane or function_plot instead of describing the graph in words."
            )
            lines.append(
                "Solve physics problems in this order: identify givens, draw the diagram, choose equations, substitute carefully, then check units and direction."
            )
            lines.append("Do not jump straight to formulas when a diagram would reduce confusion.")

    if "web_search" in capabilities:
        lines.append("")
        lines.append("WEB SEARCH INSTRUCTIONS:")
        lines.append(
            "You can search the web for current information. Use this for recent data, news, or facts you're unsure about."
        )

    if "sandbox" in capabilities:
        lines.append("")
        lines.append("CODE SANDBOX INSTRUCTIONS:")
        lines.append(
            "You can execute code in a sandboxed environment. Use this to demonstrate algorithms, run experiments, or verify solutions."
        )

    # Memory instructions
    lines.append("")
    lines.append("MEMORY:")
    lines.append("Reference what the student has covered in previous sessions when relevant.")
    lines.append("Build on prior knowledge rather than repeating basics unless asked.")
    lines.append(
        "Track which topics the student finds difficult and revisit them with different approaches."
    )

    # General behavior
    lines.append("")
    lines.append("BEHAVIOR:")
    lines.append("Be encouraging but honest. If the student makes an error, correct it clearly.")
    lines.append(
        "Keep responses concise for voice interaction. Elaborate only when the student asks."
    )
    lines.append("Ask clarifying questions when the student's request is ambiguous.")

    return "\n".join(lines)


def append_resource_context(system_prompt: str, resource_names: list[str]) -> str:
    """
    Append resource-awareness instructions to an agent system prompt.

    Args:
        system_prompt: Existing compiled system prompt.
        resource_names: List of resource names (filenames / URLs).

    Returns:
        Updated system prompt with resource instructions.
    """
    if not resource_names:
        return system_prompt

    names_list = ", ".join(resource_names)
    lines = [
        "",
        "RESOURCES:",
        f"You have access to the following resources: {names_list}.",
        "When relevant, use the search_resources tool to look up information from these resources.",
        "Cite the resource name when referencing information from them.",
    ]
    return system_prompt + "\n".join(lines)


def append_mastery_context(system_prompt: str, mastery_context: str) -> str:
    """
    Append caller-prepared mastery context to an agent system prompt.

    Args:
        system_prompt: Existing compiled system prompt.
        mastery_context: Prebuilt text block describing known strengths, struggles,
            or prior-session tutoring signals.

    Returns:
        Updated system prompt with tutoring-specific mastery guidance.
    """
    if not mastery_context or not mastery_context.strip():
        return system_prompt

    lines = [
        "",
        "MASTERY CONTEXT:",
        mastery_context.strip(),
        "Use this to adjust depth, pace, and examples.",
        "Reinforce weak areas gently, and do not waste time re-teaching topics the student already handles well unless needed.",
    ]
    return system_prompt + "\n".join(lines)


def append_storyboard_tool_context(system_prompt: str) -> str:
    """Add current visual-routing guidance to stored agent prompts at runtime."""

    return system_prompt + "\n".join(
        [
            "",
            "VERIFIED PROJECTILE STORYBOARD:",
            "For a same-speed projectile comparison launched and landed at the same height "
            "with no drag, use start_projectile_storyboard instead of teach_with_visuals.",
            "If the learner omits values, use 20 m/s and 30°/60°. Copy the learner's requested "
            "teaching direction into the tool prompt.",
            "After starting the storyboard, acknowledge the visual handoff briefly and do not "
            "repeat a competing prose lesson.",
            "For every unsupported subject or physics setup, keep using the normal conversation "
            "and teach_with_visuals path.",
        ]
    )


def get_agent_tools(capabilities: list[str]) -> list[str]:
    """
    Map capability strings to tool names.

    Args:
        capabilities: List like ["canvas", "web_search", "sandbox"].

    Returns:
        List of tool name strings to include for this agent.
    """
    tool_map = {
        "canvas": [
            "canvas_update",
            "teach_with_visuals",
            "start_projectile_storyboard",
        ],
        "web_search": ["web_search"],
        "sandbox": ["code_execution"],
    }

    tools = []
    for cap in capabilities:
        tools.extend(tool_map.get(cap, []))
    return tools
