if not _G.ui then
    local ui = menu.new({
        bots = {
            ""
        }
    }, { title = "luaxite" })
    ui:open()
    _G.ui = ui
end

return _G.ui
