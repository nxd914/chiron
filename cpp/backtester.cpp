#include "include/backtester.hpp"
#include <iostream>

namespace microstructure {

Backtester::Backtester() 
    : current_time_(0), pnl_(0.0), position_(0.0), 
      best_bid_(0.0), best_ask_(0.0), bid_vol_(0.0), ask_vol_(0.0) {}

void Backtester::add_market_data(int64_t timestamp_mu, double bid_price, double bid_volume, double ask_price, double ask_volume) {
    Event e;
    e.timestamp_mu = timestamp_mu;
    e.type = EventType::MARKET_DATA;
    e.bid_price = bid_price;
    e.bid_volume = bid_volume;
    e.ask_price = ask_price;
    e.ask_volume = ask_volume;
    // default other fields just in case
    e.order_id = 0;
    e.side = 0;
    e.quantity = 0.0;
    e.price = 0.0;
    events_.push(e);
}

void Backtester::place_order(int64_t timestamp_mu, int order_id, int side, double quantity) {
    Event e;
    e.timestamp_mu = timestamp_mu;
    e.type = EventType::ORDER;
    e.order_id = order_id;
    e.side = side;
    e.quantity = quantity;
    
    // default other fields
    e.bid_price = 0; e.bid_volume = 0;
    e.ask_price = 0; e.ask_volume = 0;
    e.price = 0;
    
    events_.push(e);
}

void Backtester::run() {
    while (!events_.empty()) {
        Event e = events_.top();
        events_.pop();

        current_time_ = e.timestamp_mu;

        if (e.type == EventType::MARKET_DATA) {
            process_market_data(e);
        } else if (e.type == EventType::ORDER) {
            process_order(e);
        } else if (e.type == EventType::FILL) {
            process_fill(e);
        }
    }
}

void Backtester::process_market_data(const Event& e) {
    best_bid_ = e.bid_price;
    best_ask_ = e.ask_price;
    bid_vol_ = e.bid_volume;
    ask_vol_ = e.ask_volume;
}

void Backtester::process_order(const Event& e) {
    // Simulate 5ms (5000 microseconds) latency before execution
    Event fill = e;
    fill.type = EventType::FILL;
    fill.timestamp_mu += 5000; 

    // Simple slippage/fill logic
    if (e.side == 1) { // Buy
        fill.price = (best_ask_ > 0) ? best_ask_ : best_bid_; 
    } else { // Sell
        fill.price = (best_bid_ > 0) ? best_bid_ : best_ask_;
    }
    
    events_.push(fill);
}

void Backtester::process_fill(const Event& e) {
    Trade t;
    t.timestamp_mu = e.timestamp_mu;
    t.order_id = e.order_id;
    t.side = e.side;
    t.quantity = e.quantity;
    t.price = e.price;
    trades_.push_back(t);

    position_ += e.side * e.quantity;
    pnl_ -= e.side * e.quantity * e.price;
}

std::vector<Trade> Backtester::get_trades() const {
    return trades_;
}

double Backtester::get_pnl() const {
    // Mark-to-market value of current position
    double mtm_price = 0.0;
    if (best_bid_ > 0 && best_ask_ > 0) {
        mtm_price = (best_bid_ + best_ask_) / 2.0;
    } else if (best_bid_ > 0) {
        mtm_price = best_bid_;
    } else if (best_ask_ > 0) {
        mtm_price = best_ask_;
    }

    if (mtm_price > 0) {
        return pnl_ + (position_ * mtm_price);
    }
    return pnl_;
}

} // namespace microstructure
