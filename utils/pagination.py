"""
utils/pagination.py — Paginated embed view .
"""

from __future__ import annotations

import discord


class PaginatedView(discord.ui.View):
    """
    A discord.ui.View that displays a list of embeds one page at a time.

    Usage:
        pages = [embed1, embed2, embed3]
        view = PaginatedView(pages)
        await interaction.followup.send(embed=pages[0], view=view)
    """

    def __init__(self, pages: list[discord.Embed], timeout: float = 120.0):
        super().__init__(timeout=timeout)
        self.pages = pages
        self.current = 0
        self.message: discord.Message | None = None
        self._update_buttons()

    def _update_buttons(self):
        """Enable/disable navigation buttons based on current page."""
        self.first_page.disabled = self.current == 0
        self.prev_page.disabled = self.current == 0
        self.next_page.disabled = self.current >= len(self.pages) - 1
        self.last_page.disabled = self.current >= len(self.pages) - 1
        self.page_counter.label = f"{self.current + 1} / {len(self.pages)}"

    async def _update_message(self, interaction: discord.Interaction):
        self._update_buttons()
        await interaction.response.edit_message(
            embed=self.pages[self.current], view=self
        )

    @discord.ui.button(emoji="⏮️", style=discord.ButtonStyle.grey, custom_id="pag_first")
    async def first_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current = 0
        await self._update_message(interaction)

    @discord.ui.button(emoji="◀️", style=discord.ButtonStyle.blurple, custom_id="pag_prev")
    async def prev_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current = max(0, self.current - 1)
        await self._update_message(interaction)

    @discord.ui.button(label="1 / 1", style=discord.ButtonStyle.grey, disabled=True, custom_id="pag_counter")
    async def page_counter(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()

    @discord.ui.button(emoji="▶️", style=discord.ButtonStyle.blurple, custom_id="pag_next")
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current = min(len(self.pages) - 1, self.current + 1)
        await self._update_message(interaction)

    @discord.ui.button(emoji="⏭️", style=discord.ButtonStyle.grey, custom_id="pag_last")
    async def last_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current = len(self.pages) - 1
        await self._update_message(interaction)

    async def on_timeout(self):
        """Disable all buttons when the view times out."""
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass
