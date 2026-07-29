from __future__ import annotations

import discord

from utils.mentions import no_mentions

# Persistent custom_ids (stable across restarts)
CID_TRACK_DECLINE_YES = "tracking_decline_yes"
CID_TRACK_DECLINE_NO = "tracking_decline_no"

CID_TICKET_CLOSE_YES = "ticket_close_yes"
CID_TICKET_CLOSE_NO = "ticket_close_no"

CID_HELP_MENU = "help_menu_select"
CID_FORMER_MEMBER_HELP_MENU = "former_member_help_menu_select"
CID_TRANSCRIPT_APPROVE = "transcript_approve"
CID_TRANSCRIPT_DENY = "transcript_deny"
CID_BAN_INFO_GIVE = "ban_info_give"

CID_LEVEL_REQUEST_BUTTON = "level_request_button"
CID_LEVEL_REQUEST_SEND = "level_request_send"
CID_LEVEL_REQUEST_REJECT = "level_request_reject"
CID_LEVEL_REQUEST_OTHER = "level_request_other"


class TranscriptRequestView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Approve", style=discord.ButtonStyle.success, custom_id=CID_TRANSCRIPT_APPROVE)
    async def approve(self, button: discord.ui.Button, interaction: discord.Interaction):
        cog = interaction.client.get_cog("HelpCog")
        if cog:
            await cog.handle_transcript_request_decision(interaction, approved=True)
        else:
            await interaction.response.send_message(
                "Transcript requests are temporarily unavailable.",
                ephemeral=True,
                allowed_mentions=no_mentions(),
            )

    @discord.ui.button(label="Deny", style=discord.ButtonStyle.danger, custom_id=CID_TRANSCRIPT_DENY)
    async def deny(self, button: discord.ui.Button, interaction: discord.Interaction):
        cog = interaction.client.get_cog("HelpCog")
        if cog:
            await cog.handle_transcript_request_decision(interaction, approved=False)
        else:
            await interaction.response.send_message(
                "Transcript requests are temporarily unavailable.",
                ephemeral=True,
                allowed_mentions=no_mentions(),
            )


class TicketClosePromptView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Yes", style=discord.ButtonStyle.danger, custom_id=CID_TICKET_CLOSE_YES)
    async def yes(self, button: discord.ui.Button, interaction: discord.Interaction):
        cog = interaction.client.get_cog("HelpCog")
        if cog:
            await cog.handle_ticket_close_prompt(interaction, confirmed=True)
        else:
            await interaction.response.send_message(
                "Ticket controls are temporarily unavailable.",
                ephemeral=True,
                allowed_mentions=no_mentions(),
            )

    @discord.ui.button(label="No", style=discord.ButtonStyle.secondary, custom_id=CID_TICKET_CLOSE_NO)
    async def no(self, button: discord.ui.Button, interaction: discord.Interaction):
        cog = interaction.client.get_cog("HelpCog")
        if cog:
            await cog.handle_ticket_close_prompt(interaction, confirmed=False)
        else:
            await interaction.response.send_message(
                "Ticket controls are temporarily unavailable.",
                ephemeral=True,
                allowed_mentions=no_mentions(),
            )


class _HelpMenuSelect(discord.ui.Select):
    def __init__(self, exclude_values=None):
        exclude = {str(value) for value in (exclude_values or set())}
        options = [
            discord.SelectOption(
                label="Dashboard",
                value="dashboard",
                description="Return to your support overview",
            ),
            discord.SelectOption(
                label="Contact staff",
                value="mod_contact",
                description="Open a private support ticket",
            ),
            discord.SelectOption(
                label="FAQ",
                value="faq",
                description="Read common questions and answers",
            ),
            discord.SelectOption(
                label="Wanna partner?",
                value="partnership",
                description="Check the requirements and contact the partnership team",
            ),
            discord.SelectOption(
                label="Appeal punishment",
                value="appeal",
                description="Ask staff to reconsider a punishment",
            ),
            discord.SelectOption(
                label="Report a user",
                value="report",
                description="Privately report harmful behavior",
            ),
            discord.SelectOption(
                label="Report a bot issue",
                value="bot_issue",
                description="Tell us about a broken command or workflow",
            ),
            discord.SelectOption(
                label="Check my weekly status",
                value="weekly_status",
                description="See this week's message count and rank",
            ),
            discord.SelectOption(
                label="Request transcript",
                value="transcript",
                description="Ask for a copy of one of your staff tickets",
            ),
            discord.SelectOption(
                label="My submissions",
                value="submission_status",
                description="Check recent appeals, reports, bugs, and transcripts",
            ),
        ]
        options = [option for option in options if option.value not in exclude]
        super().__init__(
            placeholder="Select what you need help with…",
            min_values=1,
            max_values=1,
            options=options,
            custom_id=CID_HELP_MENU,
        )

    async def callback(self, interaction: discord.Interaction):
        cog = interaction.client.get_cog("HelpCog")
        if cog:
            await cog.handle_help_selection(interaction, self.values[0])
        else:
            await interaction.response.send_message(
                "Help system is unavailable right now. Please contact staff.",
                ephemeral=True,
                allowed_mentions=no_mentions(),
            )


class HelpMenuView(discord.ui.View):
    def __init__(self, exclude_values=None):
        super().__init__(timeout=None)
        self.add_item(_HelpMenuSelect(exclude_values=exclude_values))


class _FormerMemberHelpSelect(discord.ui.Select):
    def __init__(self, exclude_values=None):
        exclude = {str(value) for value in (exclude_values or set())}
        options = [
            discord.SelectOption(
                label="Appeal ban",
                value="ban_appeal",
                description="Submit a server ban appeal to staff",
            ),
            discord.SelectOption(
                label="I don't know why I was banned",
                value="ban_info",
                description="Ask staff to retrieve your ban information",
            ),
        ]
        options = [option for option in options if option.value not in exclude]
        super().__init__(
            placeholder="Choose what you need help with…",
            min_values=1,
            max_values=1,
            options=options,
            custom_id=CID_FORMER_MEMBER_HELP_MENU,
        )

    async def callback(self, interaction: discord.Interaction):
        cog = interaction.client.get_cog("HelpCog")
        if cog:
            await cog.handle_help_selection(interaction, self.values[0])
        else:
            await interaction.response.send_message(
                "Help system is unavailable right now. Please try again later.",
                ephemeral=True,
                allowed_mentions=no_mentions(),
            )


class FormerMemberHelpView(discord.ui.View):
    def __init__(self, exclude_values=None):
        super().__init__(timeout=None)
        self.add_item(_FormerMemberHelpSelect(exclude_values=exclude_values))


class BanInfoGiveInfoView(discord.ui.View):
    def __init__(self, disabled: bool = False):
        super().__init__(timeout=None)
        button = discord.ui.Button(
            label="Give info",
            style=discord.ButtonStyle.primary,
            custom_id=CID_BAN_INFO_GIVE,
            disabled=disabled,
        )
        button.callback = self.give_info
        self.add_item(button)

    async def give_info(self, interaction: discord.Interaction):
        cog = interaction.client.get_cog("HelpCog")
        if cog:
            await cog.handle_ban_info_button(interaction)
        else:
            await interaction.response.send_message(
                "Ban information workflow is unavailable right now.",
                ephemeral=True,
                allowed_mentions=no_mentions(),
            )


class TrackingDeclineConfirmView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Yes", style=discord.ButtonStyle.danger, custom_id=CID_TRACK_DECLINE_YES)
    async def yes(self, button: discord.ui.Button, interaction: discord.Interaction):
        cog = interaction.client.get_cog("TrackingCog")
        if cog:
            await cog.handle_decline_confirm(interaction, confirmed=True)
        else:
            await interaction.response.send_message(
                "Weekly request controls are temporarily unavailable.",
                ephemeral=True,
                allowed_mentions=no_mentions(),
            )

    @discord.ui.button(label="No", style=discord.ButtonStyle.secondary, custom_id=CID_TRACK_DECLINE_NO)
    async def no(self, button: discord.ui.Button, interaction: discord.Interaction):
        cog = interaction.client.get_cog("TrackingCog")
        if cog:
            await cog.handle_decline_confirm(interaction, confirmed=False)
        else:
            await interaction.response.send_message(
                "Weekly request controls are temporarily unavailable.",
                ephemeral=True,
                allowed_mentions=no_mentions(),
            )


class LevelRequestButtonView(discord.ui.View):
    def __init__(self, label: str = "Request your level!", disabled: bool = False):
        super().__init__(timeout=None)
        button = discord.ui.Button(
            label=label or "Request your level!",
            style=discord.ButtonStyle.primary,
            custom_id=CID_LEVEL_REQUEST_BUTTON,
            disabled=disabled,
        )
        button.callback = self.request
        self.add_item(button)

    async def request(self, interaction: discord.Interaction):
        cog = interaction.client.get_cog("RequestLevelsCog")
        if cog:
            await cog.handle_request_button(interaction)
        else:
            await interaction.response.send_message(
                "Level requests are temporarily unavailable.",
                ephemeral=True,
                allowed_mentions=no_mentions(),
            )


class LevelRequestReviewView(discord.ui.View):
    def __init__(self, disabled: bool = False):
        super().__init__(timeout=None)
        for label, style, custom_id, action in (
            ("Send", discord.ButtonStyle.success, CID_LEVEL_REQUEST_SEND, "sent"),
            ("Reject", discord.ButtonStyle.danger, CID_LEVEL_REQUEST_REJECT, "rejected"),
            ("Other", discord.ButtonStyle.secondary, CID_LEVEL_REQUEST_OTHER, "other"),
        ):
            button = discord.ui.Button(label=label, style=style, custom_id=custom_id, disabled=disabled)
            button.callback = self._make_callback(action)
            self.add_item(button)

    def _make_callback(self, action: str):
        async def _callback(interaction: discord.Interaction):
            cog = interaction.client.get_cog("RequestLevelsCog")
            if cog:
                await cog.handle_review_button(interaction, action)
            else:
                await interaction.response.send_message(
                    "Request review controls are temporarily unavailable.",
                    ephemeral=True,
                    allowed_mentions=no_mentions(),
                )
        return _callback
